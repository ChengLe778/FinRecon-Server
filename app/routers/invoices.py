# -*- coding: utf-8 -*-
"""发票管理：列表 / 新增 / 编辑 / 删除（删除联动解除核销并留痕）"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..database import get_db
from ..deps import get_operator, write_log, diff_fields, money
from ..models import Invoice, Reconciliation, BankTxn, Counterparty
from ..services import (
    apply_invoice_filters, recon_sums, invoice_remaining,
    is_invoice_effective, resolve_counterparty, build_alias_index,
    release_reconciliations,
)
from ..parsers.base import to_date, to_decimal, clean_str, is_self
from ..response import ok, BizError

router = APIRouter(prefix="/api/invoices", tags=["发票"])


class InvoiceIn(BaseModel):
    direction: str = Field(..., description="销项 / 进项")
    invoice_no: Optional[str] = None
    invoice_date: Optional[str] = None
    seller_name: Optional[str] = None
    seller_tax_id: Optional[str] = None
    buyer_name: Optional[str] = None
    buyer_tax_id: Optional[str] = None
    amount: Optional[str] = None
    tax: Optional[str] = None
    total: Optional[str] = None
    invoice_type: Optional[str] = None
    status: Optional[str] = "正常"
    counterparty_id: Optional[int] = None
    remark: Optional[str] = None


def _fingerprint(direction: str, invoice_no: Optional[str],
                 seller_tax_id: Optional[str], date_str: str, total: Decimal) -> str:
    if invoice_no:
        key = f"{direction}|{invoice_no.strip()}|{(seller_tax_id or '').strip()}|{date_str}|{total}"
    else:
        # 无发票号的手工录入，加随机因子避免与另一张无号发票撞指纹
        key = f"{direction}|MANUAL|{(seller_tax_id or '').strip()}|{date_str}|{total}|{uuid.uuid4().hex[:8]}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _serialize(inv: Invoice, recon: Dict[int, Decimal]) -> Dict[str, Any]:
    remaining = invoice_remaining(inv, recon)
    cp = inv.counterparty
    return {
        "id": inv.id,
        "direction": inv.direction,
        "invoice_no": inv.invoice_no,
        "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
        "seller_name": inv.seller_name,
        "buyer_name": inv.buyer_name,
        "seller_tax_id": inv.seller_tax_id,
        "buyer_tax_id": inv.buyer_tax_id,
        "amount": str(inv.amount),
        "tax": str(inv.tax),
        "total": str(inv.total),
        "reconciled": str(money(recon.get(inv.id, Decimal('0')))),
        "remaining": str(remaining),
        "invoice_type": inv.invoice_type,
        "status": inv.status,
        "is_negative": inv.is_negative,
        "item_count": inv.item_count,
        "effective": is_invoice_effective(inv),
        "counterparty_id": inv.counterparty_id,
        "counterparty_name": (cp.name if cp else inv.counterparty_name_raw),
        "counterparty_name_raw": inv.counterparty_name_raw,
        "source": inv.source,
        "remark": inv.remark,
        "created_at": inv.created_at.strftime("%Y-%m-%d %H:%M") if inv.created_at else None,
    }


@router.get("")
def list_invoices(
    page: int = 1, size: int = 20,
    direction: Optional[str] = None,
    keyword: Optional[str] = None,
    counterparty_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    amount_min: Optional[str] = None,
    amount_max: Optional[str] = None,
    status: Optional[str] = None,
    source: Optional[str] = None,
    sort_by: str = "invoice_date",
    sort_order: str = "desc",
    db: Session = Depends(get_db),
):
    params = dict(direction=direction, keyword=keyword,
                  counterparty_id=counterparty_id, date_from=date_from,
                  date_to=date_to, amount_min=amount_min, amount_max=amount_max,
                  status=status, source=source)
    q = apply_invoice_filters(db.query(Invoice), params)

    total = q.count()

    col = {
        "invoice_date": Invoice.invoice_date,
        "total": Invoice.total,
        "created_at": Invoice.created_at,
        "id": Invoice.id,
    }.get(sort_by, Invoice.invoice_date)
    q = q.order_by(col.desc() if sort_order == "desc" else col.asc())

    rows = q.offset(max(0, (page - 1) * size)).limit(size).all()

    inv_recon, _ = recon_sums(db)
    items = [_serialize(r, inv_recon) for r in rows]

    # 未核销筛选需要在算出余额后过滤（分页不精确，但全量数据下可接受）
    if status in ("open", "closed"):
        items = [x for x in items
                 if (Decimal(x["remaining"]) > 0) == (status == "open")
                 and Decimal(x["total"]) > 0]

    # 汇总（基于当前筛选全量，不受分页影响）
    all_rows = apply_invoice_filters(db.query(Invoice), params).all()
    agg = {"count": len(all_rows), "total": Decimal("0"), "remaining": Decimal("0")}
    for r in all_rows:
        if not is_invoice_effective(r):
            continue
        agg["total"] += money(r.total)
        agg["remaining"] += invoice_remaining(r, inv_recon)

    return ok({
        "items": items,
        "total": total,
        "page": page,
        "size": size,
        "summary": {"count": agg["count"],
                    "total": str(agg["total"]),
                    "remaining": str(agg["remaining"])},
    })


@router.get("/{inv_id}")
def get_invoice(inv_id: int, db: Session = Depends(get_db)):
    inv = db.get(Invoice, inv_id)
    if not inv:
        raise BizError("发票不存在")
    inv_recon, _ = recon_sums(db)
    data = _serialize(inv, inv_recon)

    recs = (db.query(Reconciliation, BankTxn)
            .join(BankTxn, BankTxn.id == Reconciliation.txn_id)
            .filter(Reconciliation.invoice_id == inv_id)
            .order_by(Reconciliation.created_at.desc()).all())
    data["reconciliations"] = [{
        "id": r.id,
        "txn_id": r.txn_id,
        "amount": str(r.amount),
        "method": r.method,
        "status": r.status,
        "created_by": r.created_by,
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else None,
        "reversed_at": r.reversed_at.strftime("%Y-%m-%d %H:%M") if r.reversed_at else None,
        "txn_date": t.txn_date.isoformat() if t.txn_date else None,
        "txn_bank": t.bank,
        "txn_amount": str(t.amount),
        "txn_direction": t.direction,
        "txn_counterparty": t.counterparty_name_raw,
        "txn_memo": t.memo,
    } for r, t in recs]
    return ok(data)


@router.post("")
def create_invoice(body: InvoiceIn, db: Session = Depends(get_db),
                   operator: str = Depends(get_operator)):
    if body.direction not in ("销项", "进项"):
        raise BizError("方向必须是 销项 或 进项")

    d = to_date(body.invoice_date)
    amount = to_decimal(body.amount) or Decimal("0")
    tax = to_decimal(body.tax) or Decimal("0")
    total = to_decimal(body.total)
    if total is None:
        total = amount + tax
    if total == 0:
        raise BizError("价税合计不能为 0")

    date_str = d.isoformat() if d else ""
    fp = _fingerprint(body.direction, body.invoice_no, body.seller_tax_id, date_str, total)
    if db.query(Invoice).filter(Invoice.fingerprint == fp).first():
        raise BizError("该发票已存在（方向+发票号+开票日期+价税合计一致）")

    # 对方名称：销项取购方，进项取销方
    if body.direction == "销项":
        cp_name = body.buyer_name
    else:
        cp_name = body.seller_name

    cp_id = body.counterparty_id or resolve_counterparty(db, cp_name)

    inv = Invoice(
        direction=body.direction,
        invoice_no=clean_str(body.invoice_no),
        invoice_date=d,
        seller_name=clean_str(body.seller_name),
        seller_tax_id=clean_str(body.seller_tax_id),
        buyer_name=clean_str(body.buyer_name),
        buyer_tax_id=clean_str(body.buyer_tax_id),
        amount=amount, tax=tax, total=total,
        invoice_type=clean_str(body.invoice_type),
        status=body.status or "正常",
        is_negative=total < 0,
        item_count=1,
        counterparty_name_raw=cp_name,
        counterparty_id=cp_id,
        source="manual",
        fingerprint=fp,
        remark=clean_str(body.remark),
    )
    db.add(inv)
    db.flush()
    write_log(db, "invoice", inv.id, "create", {
        "direction": inv.direction, "invoice_no": inv.invoice_no,
        "invoice_date": date_str, "total": str(total),
        "counterparty": cp_name,
    }, operator)
    db.commit()
    return ok({"id": inv.id}, msg="发票已新增")


@router.put("/{inv_id}")
def update_invoice(inv_id: int, body: InvoiceIn,
                   db: Session = Depends(get_db),
                   operator: str = Depends(get_operator)):
    inv = db.get(Invoice, inv_id)
    if not inv:
        raise BizError("发票不存在")

    before = {k: getattr(inv, k) for k in
              ("direction", "invoice_no", "invoice_date", "seller_name",
               "buyer_name", "amount", "tax", "total", "status",
               "invoice_type", "remark", "counterparty_id")}

    inv.direction = body.direction or inv.direction
    inv.invoice_no = clean_str(body.invoice_no)
    if body.invoice_date:
        inv.invoice_date = to_date(body.invoice_date)
    inv.seller_name = clean_str(body.seller_name)
    inv.seller_tax_id = clean_str(body.seller_tax_id)
    inv.buyer_name = clean_str(body.buyer_name)
    inv.buyer_tax_id = clean_str(body.buyer_tax_id)
    inv.amount = to_decimal(body.amount) or inv.amount
    inv.tax = to_decimal(body.tax) or inv.tax
    if body.total is not None:
        inv.total = to_decimal(body.total) or inv.total
    if body.invoice_type is not None:
        inv.invoice_type = clean_str(body.invoice_type)
    if body.status is not None:
        inv.status = body.status
    inv.is_negative = money(inv.total) < 0
    inv.remark = clean_str(body.remark)
    inv.counterparty_name_raw = (body.buyer_name if inv.direction == "销项"
                                 else body.seller_name)
    if body.counterparty_id is not None:
        inv.counterparty_id = body.counterparty_id
    elif inv.counterparty_id is None:
        inv.counterparty_id = resolve_counterparty(db, inv.counterparty_name_raw)

    changes = diff_fields(type("B", (), before)(), {
        "direction": inv.direction, "invoice_no": inv.invoice_no,
        "invoice_date": inv.invoice_date, "seller_name": inv.seller_name,
        "buyer_name": inv.buyer_name, "amount": inv.amount, "tax": inv.tax,
        "total": inv.total, "status": inv.status,
        "invoice_type": inv.invoice_type, "remark": inv.remark,
        "counterparty_id": inv.counterparty_id,
    }, ["direction", "invoice_no", "invoice_date", "seller_name", "buyer_name",
        "amount", "tax", "total", "status", "invoice_type", "remark",
        "counterparty_id"])

    # 金额改小到已核销金额以下时，提示风险
    inv_recon, _ = recon_sums(db)
    reconciled = money(inv_recon.get(inv.id, Decimal("0")))
    if reconciled > money(inv.total):
        raise BizError(
            f"该发票已核销 {reconciled} 元，价税合计不能低于已核销金额。"
            f"请先撤销对应的核销记录。")

    db.add(inv)
    write_log(db, "invoice", inv.id, "update", changes, operator)
    db.commit()
    return ok({"id": inv.id, "changes": len(changes)}, msg="发票已更新")


@router.delete("/{inv_id}")
def delete_invoice(inv_id: int, force: bool = False,
                   db: Session = Depends(get_db),
                   operator: str = Depends(get_operator)):
    inv = db.get(Invoice, inv_id)
    if not inv:
        raise BizError("发票不存在")

    inv_recon, _ = recon_sums(db)
    reconciled = money(inv_recon.get(inv.id, Decimal("0")))
    active_cnt = db.query(func.count(Reconciliation.id)).filter(
        Reconciliation.invoice_id == inv_id,
        Reconciliation.status == "active").scalar() or 0

    if active_cnt and not force:
        raise BizError(
            f"该发票已参与 {active_cnt} 条核销（合计 {reconciled} 元），"
            f"删除将自动解除这些核销关系并回滚流水未核销金额。请确认后重试。",
            code=2, data={"recon_count": active_cnt,
                          "reconciled": str(reconciled)})

    snapshot = {
        "direction": inv.direction, "invoice_no": inv.invoice_no,
        "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
        "counterparty": inv.counterparty_name_raw,
        "total": str(inv.total), "amount": str(inv.amount), "tax": str(inv.tax),
        "source": inv.source,
    }
    released = release_reconciliations(db, invoice_id=inv_id,
                                       reason=f"删除发票 #{inv_id}")

    db.delete(inv)
    write_log(db, "invoice", inv_id, "delete", {
        "snapshot": snapshot,
        "released_reconciliations": released,
        "released_count": len(released),
    }, operator)
    db.commit()
    msg = "发票已删除"
    if released:
        msg += f"，并解除 {len(released)} 条核销关系"
    return ok({"released": len(released)}, msg=msg)
