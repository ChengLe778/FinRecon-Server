# -*- coding: utf-8 -*-
"""银行流水管理：列表 / 新增 / 编辑 / 删除（删除联动解除核销并回滚发票已核销额）"""
from __future__ import annotations

import hashlib
import uuid
from decimal import Decimal
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..database import get_db
from ..deps import get_operator, write_log, diff_fields, money
from ..models import BankTxn, Reconciliation, Invoice
from ..services import apply_txn_filters, recon_sums, txn_remaining, \
    resolve_counterparty, release_reconciliations
from ..parsers.base import to_date, to_decimal, clean_str, to_time
from ..config import BANK_ABC, BANK_BOC, BANK_CCB
from ..response import ok, BizError

router = APIRouter(prefix="/api/txns", tags=["银行流水"])

BANKS = [BANK_ABC, BANK_BOC, BANK_CCB]


class TxnIn(BaseModel):
    bank: str
    txn_date: str
    direction: str = Field(..., description="收 / 支")
    amount: str
    counterparty_name: Optional[str] = None
    counterparty_account: Optional[str] = None
    memo: Optional[str] = None
    purpose: Optional[str] = None
    account_no: Optional[str] = None
    txn_time: Optional[str] = None
    counterparty_id: Optional[int] = None
    remark: Optional[str] = None


def _serialize(t: BankTxn, recon: Dict[int, Decimal]) -> Dict[str, Any]:
    cp = t.counterparty
    return {
        "id": t.id,
        "bank": t.bank,
        "account_no": t.account_no,
        "txn_date": t.txn_date.isoformat() if t.txn_date else None,
        "txn_time": t.txn_time,
        "direction": t.direction,
        "amount": str(t.amount),
        "reconciled": str(money(recon.get(t.id, Decimal('0')))),
        "remaining": str(txn_remaining(t, recon)),
        "balance": str(t.balance) if t.balance is not None else None,
        "counterparty_name_raw": t.counterparty_name_raw,
        "counterparty_account": t.counterparty_account,
        "counterparty_id": t.counterparty_id,
        "counterparty_name": (cp.name if cp else t.counterparty_name_raw),
        "memo": t.memo,
        "purpose": t.purpose,
        "serial_no": t.serial_no,
        "source": t.source,
        "remark": t.remark,
        "created_at": t.created_at.strftime("%Y-%m-%d %H:%M") if t.created_at else None,
    }


@router.get("")
def list_txns(
    page: int = 1, size: int = 20,
    bank: Optional[str] = None,
    direction: Optional[str] = None,
    keyword: Optional[str] = None,
    counterparty_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    amount_min: Optional[str] = None,
    amount_max: Optional[str] = None,
    status: Optional[str] = None,
    source: Optional[str] = None,
    sort_by: str = "txn_date",
    sort_order: str = "desc",
    db: Session = Depends(get_db),
):
    params = dict(bank=bank, direction=direction, keyword=keyword,
                  counterparty_id=counterparty_id, date_from=date_from,
                  date_to=date_to, amount_min=amount_min, amount_max=amount_max,
                  source=source)
    q = apply_txn_filters(db.query(BankTxn), params)
    total = q.count()

    col = {"txn_date": BankTxn.txn_date, "amount": BankTxn.amount,
           "created_at": BankTxn.created_at, "id": BankTxn.id}.get(
        sort_by, BankTxn.txn_date)
    q = q.order_by(col.desc() if sort_order == "desc" else col.asc())
    rows = q.offset(max(0, (page - 1) * size)).limit(size).all()

    _, txn_recon = recon_sums(db)
    items = [_serialize(r, txn_recon) for r in rows]

    if status in ("open", "closed"):
        items = [x for x in items
                 if (Decimal(x["remaining"]) > 0) == (status == "open")]

    all_rows = apply_txn_filters(db.query(BankTxn), params).all()
    income = sum((money(r.amount) for r in all_rows if r.direction == "收"), Decimal("0"))
    expense = sum((money(r.amount) for r in all_rows if r.direction == "支"), Decimal("0"))

    return ok({
        "items": items, "total": total, "page": page, "size": size,
        "summary": {"count": len(all_rows), "income": str(income),
                    "expense": str(expense), "net": str(income - expense)},
    })


@router.get("/banks")
def list_banks(db: Session = Depends(get_db)):
    rows = db.query(BankTxn.bank, func.count(BankTxn.id)).group_by(BankTxn.bank).all()
    known = {b: 0 for b in BANKS}
    for b, c in rows:
        known[b] = c
    return ok([{"bank": k, "count": v} for k, v in known.items()])


@router.get("/{txn_id}")
def get_txn(txn_id: int, db: Session = Depends(get_db)):
    t = db.get(BankTxn, txn_id)
    if not t:
        raise BizError("流水不存在")
    _, txn_recon = recon_sums(db)
    data = _serialize(t, txn_recon)
    recs = (db.query(Reconciliation, Invoice)
            .join(Invoice, Invoice.id == Reconciliation.invoice_id)
            .filter(Reconciliation.txn_id == txn_id)
            .order_by(Reconciliation.created_at.desc()).all())
    data["reconciliations"] = [{
        "id": r.id, "invoice_id": r.invoice_id, "amount": str(r.amount),
        "method": r.method, "status": r.status, "created_by": r.created_by,
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else None,
        "reversed_at": r.reversed_at.strftime("%Y-%m-%d %H:%M") if r.reversed_at else None,
        "invoice_no": i.invoice_no, "invoice_date": i.invoice_date.isoformat() if i.invoice_date else None,
        "invoice_total": str(i.total), "invoice_direction": i.direction,
        "invoice_counterparty": i.counterparty_name_raw,
    } for r, i in recs]
    return ok(data)


@router.post("")
def create_txn(body: TxnIn, db: Session = Depends(get_db),
               operator: str = Depends(get_operator)):
    if body.direction not in ("收", "支"):
        raise BizError("方向必须是 收 或 支")
    d = to_date(body.txn_date)
    if not d:
        raise BizError("交易日期格式不正确")
    amount = to_decimal(body.amount)
    if not amount or amount <= 0:
        raise BizError("金额必须大于 0")

    fp = hashlib.sha256(
        f"MANUAL|{body.bank}|{body.account_no or ''}|{d.isoformat()}|"
        f"{body.txn_time or ''}|{amount}|{body.direction}|{uuid.uuid4().hex[:8]}"
        .encode("utf-8")).hexdigest()

    cp_id = body.counterparty_id or resolve_counterparty(db, body.counterparty_name)
    t = BankTxn(
        bank=body.bank,
        account_no=clean_str(body.account_no),
        txn_date=d,
        txn_time=to_time(body.txn_time),
        direction=body.direction,
        amount=amount,
        counterparty_name_raw=clean_str(body.counterparty_name),
        counterparty_account=clean_str(body.counterparty_account),
        counterparty_id=cp_id,
        memo=clean_str(body.memo),
        purpose=clean_str(body.purpose),
        source="manual",
        fingerprint=fp,
        remark=clean_str(body.remark),
    )
    db.add(t)
    db.flush()
    write_log(db, "bank_txn", t.id, "create", {
        "bank": t.bank, "txn_date": d.isoformat(), "direction": t.direction,
        "amount": str(amount), "counterparty": t.counterparty_name_raw,
    }, operator)
    db.commit()
    return ok({"id": t.id}, msg="流水已新增")


@router.put("/{txn_id}")
def update_txn(txn_id: int, body: TxnIn, db: Session = Depends(get_db),
               operator: str = Depends(get_operator)):
    t = db.get(BankTxn, txn_id)
    if not t:
        raise BizError("流水不存在")

    before = {k: getattr(t, k) for k in
              ("bank", "txn_date", "direction", "amount",
               "counterparty_name_raw", "memo", "purpose", "remark")}

    t.bank = body.bank or t.bank
    if body.txn_date:
        d = to_date(body.txn_date)
        if not d:
            raise BizError("交易日期格式不正确")
        t.txn_date = d
    t.direction = body.direction or t.direction
    if body.amount:
        amt = to_decimal(body.amount)
        if not amt or amt <= 0:
            raise BizError("金额必须大于 0")
        t.amount = amt
    t.counterparty_name_raw = clean_str(body.counterparty_name)
    t.counterparty_account = clean_str(body.counterparty_account)
    t.memo = clean_str(body.memo)
    t.purpose = clean_str(body.purpose)
    t.account_no = clean_str(body.account_no) or t.account_no
    t.txn_time = to_time(body.txn_time) or t.txn_time
    t.remark = clean_str(body.remark)
    if body.counterparty_id is not None:
        t.counterparty_id = body.counterparty_id

    _, txn_recon = recon_sums(db)
    reconciled = money(txn_recon.get(t.id, Decimal("0")))
    if reconciled > money(t.amount):
        raise BizError(
            f"该流水已核销 {reconciled} 元，金额不能低于已核销金额。请先撤销核销记录。")

    changes = diff_fields(type("B", (), before)(), {
        "bank": t.bank, "txn_date": t.txn_date, "direction": t.direction,
        "amount": t.amount, "counterparty_name_raw": t.counterparty_name_raw,
        "memo": t.memo, "purpose": t.purpose, "remark": t.remark,
    }, ["bank", "txn_date", "direction", "amount", "counterparty_name_raw",
        "memo", "purpose", "remark"])

    db.add(t)
    write_log(db, "bank_txn", t.id, "update", changes, operator)
    db.commit()
    return ok({"id": t.id, "changes": len(changes)}, msg="流水已更新")


@router.delete("/{txn_id}")
def delete_txn(txn_id: int, force: bool = False, db: Session = Depends(get_db),
               operator: str = Depends(get_operator)):
    t = db.get(BankTxn, txn_id)
    if not t:
        raise BizError("流水不存在")

    _, txn_recon = recon_sums(db)
    reconciled = money(txn_recon.get(t.id, Decimal("0")))
    active_cnt = db.query(func.count(Reconciliation.id)).filter(
        Reconciliation.txn_id == txn_id, Reconciliation.status == "active").scalar() or 0

    if active_cnt and not force:
        raise BizError(
            f"该流水已参与 {active_cnt} 条核销（合计 {reconciled} 元），"
            f"删除将自动解除核销并回滚发票已核销金额。请确认后重试。",
            code=2, data={"recon_count": active_cnt, "reconciled": str(reconciled)})

    snapshot = {
        "bank": t.bank, "txn_date": t.txn_date.isoformat() if t.txn_date else None,
        "direction": t.direction, "amount": str(t.amount),
        "counterparty": t.counterparty_name_raw, "memo": t.memo,
        "source": t.source,
    }
    released = release_reconciliations(db, txn_id=txn_id,
                                       reason=f"删除流水 #{txn_id}")
    db.delete(t)
    write_log(db, "bank_txn", txn_id, "delete", {
        "snapshot": snapshot,
        "released_reconciliations": released,
        "released_count": len(released),
    }, operator)
    db.commit()
    msg = "流水已删除"
    if released:
        msg += f"，并解除 {len(released)} 条核销关系"
    return ok({"released": len(released)}, msg=msg)
