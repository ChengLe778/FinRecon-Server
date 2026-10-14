# -*- coding: utf-8 -*-
"""对账：跑引擎 / 确认核销 / 手工指定 / 撤销"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_

from ..database import get_db
from ..deps import get_operator, write_log, money
from ..models import Reconciliation, Invoice, BankTxn, Counterparty
from ..services import recon_sums, invoice_remaining, txn_remaining, \
    is_invoice_effective, counterparty_key, recon_diff_sum
from ..engine.reconcile import match, InvSide, TxnSide
from ..response import ok, BizError

router = APIRouter(prefix="/api/reconcile", tags=["对账"])

ZERO = Decimal("0.00")

# 应收：销项发票 × 收款流水；应付：进项发票 × 付款流水
SCOPE = {
    "ar": {"invoice_direction": "销项", "txn_direction": "收", "label": "应收"},
    "ap": {"invoice_direction": "进项", "txn_direction": "支", "label": "应付"},
}


class PairIn(BaseModel):
    invoice_ids: List[int]
    txn_ids: List[int]
    amount: str
    method: str = "manual"
    remark: Optional[str] = None


def _build_sides(db: Session, scope: str, counterparty_id: Optional[int] = None,
                 date_from: Optional[str] = None, date_to: Optional[str] = None):
    cfg = SCOPE[scope]
    inv_recon, txn_recon = recon_sums(db)

    iq = db.query(Invoice).filter(Invoice.direction == cfg["invoice_direction"])
    tq = db.query(BankTxn).filter(BankTxn.direction == cfg["txn_direction"])
    if counterparty_id:
        iq = iq.filter(Invoice.counterparty_id == counterparty_id)
        tq = tq.filter(BankTxn.counterparty_id == counterparty_id)
    if date_from:
        iq = iq.filter(Invoice.invoice_date >= date_from)
        tq = tq.filter(BankTxn.txn_date >= date_from)
    if date_to:
        iq = iq.filter(Invoice.invoice_date <= date_to)
        tq = tq.filter(BankTxn.txn_date <= date_to)

    inv_sides, txn_sides = [], []
    for inv in iq.options(joinedload(Invoice.counterparty)).all():
        if not is_invoice_effective(inv) or money(inv.total) <= 0:
            continue                      # 已红冲/红字发票不参与匹配
        rem = invoice_remaining(inv, inv_recon)
        if rem <= 0:
            continue
        inv_sides.append(InvSide(
            id=inv.id, invoice_no=inv.invoice_no,
            invoice_date=inv.invoice_date.isoformat() if inv.invoice_date else None,
            total=money(inv.total), remaining=rem,
            cp_key=counterparty_key(inv),
            cp_name=(inv.counterparty.name if inv.counterparty
                     else inv.counterparty_name_raw),
            direction=inv.direction,
            category=inv.category,
        ))
    for t in tq.options(joinedload(BankTxn.counterparty)).all():
        rem = txn_remaining(t, txn_recon)
        if rem <= 0 or money(t.amount) <= 0:
            continue
        txn_sides.append(TxnSide(
            id=t.id, txn_date=t.txn_date.isoformat() if t.txn_date else None,
            amount=money(t.amount), remaining=rem,
            cp_key=counterparty_key(t),
            cp_name=(t.counterparty.name if t.counterparty
                     else t.counterparty_name_raw),
            bank=t.bank, memo=t.memo, direction=t.direction,
        ))
    return inv_sides, txn_sides


def _pair_out(p) -> dict:
    return {
        "kind": p.kind,
        "invoice_ids": p.invoice_ids,
        "txn_ids": p.txn_ids,
        "amount": str(p.amount),
        "score": p.score,
        "reason": p.reason,
        "diff_amount": str(getattr(p, "diff_amount", 0) or 0),
        "action": getattr(p, "action", ""),
        "action_label": getattr(p, "action_label", ""),
        "detail": p.detail,
    }


@router.post("/run")
def run(payload: dict, db: Session = Depends(get_db),
        operator: str = Depends(get_operator)):
    """跑对账引擎。scope: ar / ap / all；auto_apply=true 时自动核销精确匹配。"""
    scope = (payload or {}).get("scope", "all")
    auto_apply = bool((payload or {}).get("auto_apply", False))
    counterparty_id = (payload or {}).get("counterparty_id")
    date_from = (payload or {}).get("date_from")
    date_to = (payload or {}).get("date_to")

    scopes = ["ar", "ap"] if scope == "all" else [scope]
    if scope not in ("ar", "ap", "all"):
        raise BizError("scope 只能是 ar / ap / all")

    result = {"ar": None, "ap": None}
    applied_total = {"ar": 0, "ap": 0}

    for s in scopes:
        inv_sides, txn_sides = _build_sides(db, s, counterparty_id, date_from, date_to)
        res = match(inv_sides, txn_sides)
        applied = 0
        if auto_apply and res["auto"]:
            applied = _apply_pairs(db, [p for p in res["auto"]], "auto", operator)
        result[s] = {
            "label": SCOPE[s]["label"],
            "auto": [_pair_out(p) for p in res["auto"]],
            "diffs": [_pair_out(p) for p in res["diffs"]],
            "candidates": [_pair_out(p) for p in res["candidates"]],
            "stats": res["stats"],
            "applied": applied,
        }
        applied_total[s] = applied

    if auto_apply:
        db.commit()
        write_log(db, "reconciliation", None, "reconcile", {
            "scope": scope, "auto_apply": True,
            "ar_applied": applied_total["ar"], "ap_applied": applied_total["ap"],
        }, operator)
        db.commit()

    return ok(result, msg=("已自动核销 " +
                           "、".join(f"{SCOPE[s]['label']} {applied_total[s]} 条"
                                     for s in scopes if applied_total[s]))
              if auto_apply and sum(applied_total.values()) else "对账完成")


def _apply_pairs(db: Session, pairs: list, method: str, operator: str,
                 remark: Optional[str] = None) -> int:
    """写入核销记录，逐对校验两侧未核销余额。

    pairs 元素支持 dict：{invoice_ids, txn_ids, amount, diff_amount?, diff_reason?}
    diff_amount 为「差异金额」：发票侧按 amount+diff_amount 结清，流水侧只认 amount，
    用于抹零 / 手续费 / 汇损等钱没全额到账但业务已结清的场景。
    """
    inv_recon, txn_recon = recon_sums(db)
    n = 0
    for p in pairs:
        amount = money(p.amount if hasattr(p, "amount") else p["amount"])
        if amount <= 0:
            continue
        inv_ids = p.invoice_ids if hasattr(p, "invoice_ids") else p["invoice_ids"]
        txn_ids = p.txn_ids if hasattr(p, "txn_ids") else p["txn_ids"]
        diff_amt = money((p.get("diff_amount") if isinstance(p, dict) else None) or 0)
        diff_reason = (p.get("diff_reason") if isinstance(p, dict) else None) or None

        # 按传入金额在发票侧/流水侧分配
        invs = db.query(Invoice).filter(Invoice.id.in_(inv_ids)).all()
        txns = db.query(BankTxn).filter(BankTxn.id.in_(txn_ids)).all()
        if not invs or not txns:
            continue

        inv_left = {i.id: invoice_remaining(i, inv_recon) for i in invs}
        txn_left = {t.id: txn_remaining(t, txn_recon) for t in txns}
        need = amount
        if sum(inv_left.values()) < need or sum(txn_left.values()) < need:
            need = min(sum(inv_left.values()), sum(txn_left.values()))
        if need <= 0:
            continue

        # 依次把 need 拆到各发票/流水上
        inv_plan, rest = [], need
        for i in sorted(invs, key=lambda x: x.id):
            if rest <= 0:
                break
            take = min(inv_left[i.id], rest)
            if take > 0:
                inv_plan.append((i.id, take))
                rest -= take
        txn_plan, rest2 = [], need
        for t in sorted(txns, key=lambda x: x.id):
            if rest2 <= 0:
                break
            take = min(txn_left[t.id], rest2)
            if take > 0:
                txn_plan.append((t.id, take))
                rest2 -= take

        # 发票与流水两两配对，金额按最小可用量分配
        ii, ti = 0, 0
        ia, ta = (inv_plan[0][1] if inv_plan else Decimal("0")), \
                 (txn_plan[0][1] if txn_plan else Decimal("0"))
        remain_need = need
        created: List[Reconciliation] = []
        while remain_need > 0 and ii < len(inv_plan) and ti < len(txn_plan):
            take = min(ia, ta, remain_need)
            if take <= 0:
                if ia <= 0:
                    ii += 1
                    ia = inv_plan[ii][1] if ii < len(inv_plan) else Decimal("0")
                else:
                    ti += 1
                    ta = txn_plan[ti][1] if ti < len(txn_plan) else Decimal("0")
                continue
            rec = Reconciliation(
                invoice_id=inv_plan[ii][0], txn_id=txn_plan[ti][0],
                amount=take, method=method, status="active",
                created_by=operator, remark=remark,
            )
            db.add(rec)
            created.append(rec)
            inv_recon[inv_plan[ii][0]] = inv_recon.get(inv_plan[ii][0], Decimal("0")) + take
            txn_recon[txn_plan[ti][0]] = txn_recon.get(txn_plan[ti][0], Decimal("0")) + take
            ia -= take
            ta -= take
            remain_need -= take
            n += 1
            if ia <= 0:
                ii += 1
                ia = inv_plan[ii][1] if ii < len(inv_plan) else Decimal("0")
            if ta <= 0:
                ti += 1
                ta = txn_plan[ti][1] if ti < len(txn_plan) else Decimal("0")

        # 差异金额：整对只记一次，挂在首条核销记录上
        if diff_amt > 0 and created:
            created[0].diff_amount = diff_amt
            created[0].diff_reason = diff_reason or "差异核销"
            inv_recon[created[0].invoice_id] = (
                inv_recon.get(created[0].invoice_id, Decimal("0")) + diff_amt)
    return n


@router.post("/confirm")
def confirm(payload: dict, db: Session = Depends(get_db),
            operator: str = Depends(get_operator)):
    """确认候选核销 / 手工指定核销。"""
    pairs = (payload or {}).get("pairs") or []
    method = (payload or {}).get("method") or "manual"
    remark = (payload or {}).get("remark")
    if not pairs:
        raise BizError("没有要核销的记录")

    norm = []
    for p in pairs:
        amt = money(p.get("amount"))
        if amt <= 0:
            raise BizError("核销金额必须大于 0")
        diff_amt = money(p.get("diff_amount") or 0)
        norm.append({
            "invoice_ids": [int(x) for x in p.get("invoice_ids") or []],
            "txn_ids": [int(x) for x in p.get("txn_ids") or []],
            "amount": amt,
            "diff_amount": diff_amt,
            "diff_reason": p.get("diff_reason") or p.get("remark"),
        })
        if not norm[-1]["invoice_ids"] or not norm[-1]["txn_ids"]:
            raise BizError("每次核销必须同时指定发票与流水")

    try:
        n = _apply_pairs(db, norm, method, operator, remark)
    except Exception as e:
        db.rollback()
        raise BizError(f"核销失败：{e}")

    write_log(db, "reconciliation", None, "reconcile", {
        "method": method, "pairs": len(norm), "created": n, "remark": remark,
        "diff_amount": str(sum((x["diff_amount"] for x in norm), Decimal("0"))),
    }, operator)
    db.commit()
    return ok({"created": n}, msg=f"已生成 {n} 条核销记录")


@router.post("/{recon_id}/reverse")
def reverse(recon_id: int, payload: dict = None, db: Session = Depends(get_db),
            operator: str = Depends(get_operator)):
    """撤销核销：不改原记录，插入一条 reversed 记录并回填 reverse_of。"""
    r = db.get(Reconciliation, recon_id)
    if not r:
        raise BizError("核销记录不存在")
    if r.status != "active":
        raise BizError("该核销记录已被撤销")

    now = datetime.now()
    r.status = "reversed"
    r.reversed_at = now

    new = Reconciliation(
        invoice_id=r.invoice_id, txn_id=r.txn_id, amount=r.amount,
        method=r.method, status="reversed", created_by=operator,
        created_at=now, reversed_at=now, reverse_of=r.id,
        remark=(payload or {}).get("reason") or f"撤销核销 #{r.id}",
    )
    db.add(new)
    write_log(db, "reconciliation", r.id, "reverse", {
        "invoice_id": r.invoice_id, "txn_id": r.txn_id,
        "amount": str(r.amount),
        "reason": (payload or {}).get("reason"),
    }, operator)
    db.commit()
    return ok({"id": new.id}, msg=f"已撤销核销，释放金额 {r.amount} 元")


@router.get("/records")
def records(
    page: int = 1, size: int = 20,
    status: Optional[str] = None,
    method: Optional[str] = None,
    counterparty_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    q = db.query(Reconciliation, Invoice, BankTxn).join(
        Invoice, Invoice.id == Reconciliation.invoice_id).join(
        BankTxn, BankTxn.id == Reconciliation.txn_id)
    if status:
        q = q.filter(Reconciliation.status == status)
    if method:
        q = q.filter(Reconciliation.method == method)
    if counterparty_id:
        q = q.filter(or_(Invoice.counterparty_id == counterparty_id,
                         BankTxn.counterparty_id == counterparty_id))
    total = q.count()
    rows = q.order_by(Reconciliation.created_at.desc()).offset(
        max(0, (page - 1) * size)).limit(size).all()

    return ok({
        "total": total, "page": page, "size": size,
        "items": [{
            "id": r.id,
            "amount": str(r.amount),
            "method": r.method,
            "status": r.status,
            "created_by": r.created_by,
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else None,
            "reversed_at": r.reversed_at.strftime("%Y-%m-%d %H:%M") if r.reversed_at else None,
            "reverse_of": r.reverse_of,
            "remark": r.remark,
            "invoice_id": i.id,
            "invoice_no": i.invoice_no,
            "invoice_date": i.invoice_date.isoformat() if i.invoice_date else None,
            "invoice_direction": i.direction,
            "invoice_total": str(i.total),
            "invoice_counterparty": (i.counterparty.name if i.counterparty
                                     else i.counterparty_name_raw),
            "txn_id": t.id,
            "txn_date": t.txn_date.isoformat() if t.txn_date else None,
            "txn_bank": t.bank,
            "txn_direction": t.direction,
            "txn_amount": str(t.amount),
            "txn_counterparty": (t.counterparty.name if t.counterparty
                                 else t.counterparty_name_raw),
            "txn_memo": t.memo,
        } for r, i, t in rows],
    })


@router.get("/summary")
def summary(db: Session = Depends(get_db)):
    """对账概览：待核销发票数/流水数、已核销金额"""
    inv_recon, txn_recon = recon_sums(db)
    out = {}
    for scope, cfg in SCOPE.items():
        invs = [i for i in db.query(Invoice).filter(
            Invoice.direction == cfg["invoice_direction"]).all()
            if is_invoice_effective(i)]
        txns = db.query(BankTxn).filter(
            BankTxn.direction == cfg["txn_direction"]).all()

        inv_total = sum((money(i.total) for i in invs), Decimal("0"))
        inv_rem = sum((invoice_remaining(i, inv_recon) for i in invs), Decimal("0"))
        txn_total = sum((money(t.amount) for t in txns), Decimal("0"))
        txn_rem = sum((txn_remaining(t, txn_recon) for t in txns), Decimal("0"))

        out[scope] = {
            "label": cfg["label"],
            "invoice_count": len(invs),
            "invoice_total": str(inv_total),
            "invoice_remaining": str(inv_rem),
            "invoice_reconciled": str(inv_total - inv_rem),
            "txn_count": len(txns),
            "txn_total": str(txn_total),
            "txn_remaining": str(txn_rem),
            "open_invoice_count": sum(1 for i in invs
                                      if invoice_remaining(i, inv_recon) > 0
                                      and money(i.total) > 0),
            "open_txn_count": sum(1 for t in txns if txn_remaining(t, txn_recon) > 0),
        }
    return ok(out)


# ------------------------------------------------------------------ 对账工作台

def _cp_name_of(obj, fallback: Optional[str] = None) -> str:
    if getattr(obj, "counterparty", None):
        return obj.counterparty.name
    return obj.counterparty_name_raw or fallback or "(未认领)"


@router.get("/workbench")
def workbench(scope: str = "ar", keyword: Optional[str] = None,
              only_diff: bool = False, page: int = 1, size: int = 20,
              db: Session = Depends(get_db)):
    """单位对账工作台：同一往来单位下，发票 / 流水 / 已核销 / 差额 四栏并列。

    金额不一致不会自动核销，但一定会在这里体现：
      gap = 发票未核销额 - 流水未核销额
        0      → 两边齐平
        > 0    → 对方还欠（应收）/ 我们还欠（应付）
        < 0    → 多收/多付，流水侧挂预收/预付
    """
    if scope not in SCOPE:
        raise BizError("scope 只能是 ar / ap")
    cfg = SCOPE[scope]
    inv_recon, txn_recon = recon_sums(db)

    inv_q = db.query(Invoice).options(joinedload(Invoice.counterparty)).filter(
        Invoice.direction == cfg["invoice_direction"])
    txn_q = db.query(BankTxn).options(joinedload(BankTxn.counterparty)).filter(
        BankTxn.direction == cfg["txn_direction"])

    groups: Dict[Any, Dict[str, Any]] = {}

    def _g(key: Any, name: str, cp_id: Optional[int]):
        return groups.setdefault(key, {
            "counterparty_id": cp_id,
            "counterparty": name,
            "invoice_total": ZERO, "invoice_remaining": ZERO, "invoice_count": 0,
            "txn_total": ZERO, "txn_remaining": ZERO, "txn_count": 0,
            "first_date": None, "last_date": None,
        })

    for inv in inv_q.all():
        if not is_invoice_effective(inv):
            continue
        key = inv.counterparty_id or f"nm:{inv.counterparty_name_raw}"
        g = _g(key, _cp_name_of(inv), inv.counterparty_id)
        g["invoice_total"] += money(inv.total)
        g["invoice_remaining"] += invoice_remaining(inv, inv_recon)
        g["invoice_count"] += 1
        d = inv.invoice_date
        if d:
            g["first_date"] = min(g["first_date"], d) if g["first_date"] else d
            g["last_date"] = max(g["last_date"], d) if g["last_date"] else d

    for t in txn_q.all():
        key = t.counterparty_id or f"nm:{t.counterparty_name_raw}"
        g = _g(key, _cp_name_of(t), t.counterparty_id)
        g["txn_total"] += money(t.amount)
        g["txn_remaining"] += txn_remaining(t, txn_recon)
        g["txn_count"] += 1
        d = t.txn_date
        if d:
            g["first_date"] = min(g["first_date"], d) if g["first_date"] else d
            g["last_date"] = max(g["last_date"], d) if g["last_date"] else d

    items = []
    for g in groups.values():
        inv_rem, txn_rem = g["invoice_remaining"], g["txn_remaining"]
        gap = inv_rem - txn_rem
        reconciled = g["invoice_total"] - inv_rem
        status = ("已平" if abs(gap) < Decimal("0.01") and inv_rem == 0 else
                  "挂账" if gap > 0 else "多收/多付" if gap < 0 else "部分核销")
        items.append({
            "counterparty_id": g["counterparty_id"],
            "counterparty": g["counterparty"],
            "invoice_count": g["invoice_count"],
            "invoice_total": str(g["invoice_total"]),
            "invoice_remaining": str(inv_rem),
            "txn_count": g["txn_count"],
            "txn_total": str(g["txn_total"]),
            "txn_remaining": str(txn_rem),
            "reconciled": str(reconciled),
            "gap": str(gap),
            "status": status,
            "first_date": g["first_date"].isoformat() if g["first_date"] else None,
            "last_date": g["last_date"].isoformat() if g["last_date"] else None,
        })

    if keyword:
        kw = keyword.strip()
        items = [x for x in items if kw in (x["counterparty"] or "")]
    if only_diff:
        items = [x for x in items if abs(Decimal(x["gap"])) >= Decimal("0.01")]

    items.sort(key=lambda x: (-abs(Decimal(x["gap"])), -Decimal(x["invoice_remaining"])))
    total = len(items)
    start = max(0, (page - 1) * size)
    page_items = items[start:start + size]

    return ok({
        "scope": scope, "label": cfg["label"],
        "total": total, "page": page, "size": size,
        "items": page_items,
        "summary": {
            "counterparty_count": total,
            "invoice_total": str(sum((Decimal(x["invoice_total"]) for x in items), ZERO)),
            "invoice_remaining": str(sum((Decimal(x["invoice_remaining"]) for x in items), ZERO)),
            "txn_total": str(sum((Decimal(x["txn_total"]) for x in items), ZERO)),
            "txn_remaining": str(sum((Decimal(x["txn_remaining"]) for x in items), ZERO)),
            "gap": str(sum((Decimal(x["gap"]) for x in items), ZERO)),
            "diff_unit_count": sum(1 for x in items
                                   if abs(Decimal(x["gap"])) >= Decimal("0.01")),
        },
    })


@router.get("/workbench/detail")
def workbench_detail(scope: str = "ar", counterparty_id: Optional[int] = None,
                     name: Optional[str] = None,
                     db: Session = Depends(get_db)):
    """单个往来单位的对账明细：发票侧、流水侧、已核销、待处理建议。"""
    if scope not in SCOPE:
        raise BizError("scope 只能是 ar / ap")
    if counterparty_id is None and not name:
        raise BizError("必须指定 counterparty_id 或 name")
    cfg = SCOPE[scope]
    inv_recon, txn_recon = recon_sums(db)

    inv_q = db.query(Invoice).options(joinedload(Invoice.counterparty)).filter(
        Invoice.direction == cfg["invoice_direction"])
    txn_q = db.query(BankTxn).options(joinedload(BankTxn.counterparty)).filter(
        BankTxn.direction == cfg["txn_direction"])
    if counterparty_id is not None:
        inv_q = inv_q.filter(Invoice.counterparty_id == counterparty_id)
        txn_q = txn_q.filter(BankTxn.counterparty_id == counterparty_id)
    else:
        inv_q = inv_q.filter(Invoice.counterparty_name_raw == name)
        txn_q = txn_q.filter(BankTxn.counterparty_name_raw == name)

    invoices, txns = [], []
    cp_name = name or ""
    if not cp_name and counterparty_id is not None:
        # 该单位在当前方向下可能没有任何发票/流水（例如它是应付单位却用应收方向打开），
        # 这时仍要回显单位名称，避免前端抽屉标题空白。
        cp = db.get(Counterparty, counterparty_id)
        cp_name = cp.name if cp else ""
    for inv in inv_q.all():
        if not is_invoice_effective(inv):
            continue
        cp_name = cp_name or _cp_name_of(inv)
        rem = invoice_remaining(inv, inv_recon)
        invoices.append({
            "id": inv.id, "invoice_no": inv.invoice_no,
            "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
            "total": str(money(inv.total)), "remaining": str(rem),
            "category": inv.category, "item_names": inv.item_names,
            "status": inv.status,
        })
    for t in txn_q.all():
        cp_name = cp_name or _cp_name_of(t)
        rem = txn_remaining(t, txn_recon)
        txns.append({
            "id": t.id, "txn_date": t.txn_date.isoformat() if t.txn_date else None,
            "amount": str(money(t.amount)), "remaining": str(rem),
            "bank": t.bank, "memo": t.memo, "purpose": t.purpose,
        })

    # 已核销配对
    inv_ids = [i["id"] for i in invoices]
    txn_ids = [t["id"] for t in txns]
    pairs = []
    if inv_ids and txn_ids:
        rows = db.query(Reconciliation).filter(
            Reconciliation.status == "active",
            Reconciliation.invoice_id.in_(inv_ids),
            Reconciliation.txn_id.in_(txn_ids),
        ).all()
        imap = {i["id"]: i for i in invoices}
        tmap = {t["id"]: t for t in txns}
        for r in rows:
            pairs.append({
                "id": r.id, "amount": str(money(r.amount)),
                "diff_amount": str(money(r.diff_amount)),
                "diff_reason": r.diff_reason,
                "method": r.method,
                "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else None,
                "invoice_no": imap.get(r.invoice_id, {}).get("invoice_no"),
                "txn_date": tmap.get(r.txn_id, {}).get("txn_date"),
                "invoice_id": r.invoice_id, "txn_id": r.txn_id,
            })

    # 该单位内的匹配建议（金额不等的只给建议，不自动核销）
    inv_sides = [InvSide(
        id=i["id"], invoice_no=i["invoice_no"], invoice_date=i["invoice_date"],
        total=Decimal(i["total"]), remaining=Decimal(i["remaining"]),
        cp_key=f"cp:{counterparty_id}" if counterparty_id else f"nm:{name}",
        cp_name=cp_name, category=i["category"]) for i in invoices
        if Decimal(i["remaining"]) > 0]
    txn_sides = [TxnSide(
        id=t["id"], txn_date=t["txn_date"], amount=Decimal(t["amount"]),
        remaining=Decimal(t["remaining"]),
        cp_key=f"cp:{counterparty_id}" if counterparty_id else f"nm:{name}",
        cp_name=cp_name, bank=t["bank"], memo=t["memo"]) for t in txns
        if Decimal(t["remaining"]) > 0]
    res = match(inv_sides, txn_sides)

    return ok({
        "scope": scope, "label": cfg["label"],
        "counterparty_id": counterparty_id, "counterparty": cp_name,
        "invoices": invoices, "txns": txns, "pairs": pairs,
        "suggestions": {
            "auto": [_pair_out(p) for p in res["auto"]],
            "diffs": [_pair_out(p) for p in res["diffs"]],
        },
        "summary": {
            "invoice_total": str(sum((Decimal(i["total"]) for i in invoices), ZERO)),
            "invoice_remaining": str(sum((Decimal(i["remaining"]) for i in invoices), ZERO)),
            "txn_total": str(sum((Decimal(t["amount"]) for t in txns), ZERO)),
            "txn_remaining": str(sum((Decimal(t["remaining"]) for t in txns), ZERO)),
            "reconciled": str(sum((money(r["amount"]) for r in pairs), ZERO)),
            "diff_confirmed": str(sum((money(r["diff_amount"]) for r in pairs), ZERO)),
            "gap": str(sum((Decimal(i["remaining"]) for i in invoices), ZERO)
                       - sum((Decimal(t["remaining"]) for t in txns), ZERO)),
        },
    })
