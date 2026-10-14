# -*- coding: utf-8 -*-
"""业务服务层：查询过滤、余额计算、往来单位归属。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import or_, func, and_
from sqlalchemy.orm import Session

from .models import (
    Invoice, BankTxn, Counterparty, CounterpartyAlias,
    Reconciliation, ImportLog,
)
from .parsers.base import normalize_name
from .deps import money
from .config import AGING_BUCKETS

ZERO = Decimal("0.00")

# 发票状态中视为已作废 / 已红冲、不计入余额的关键词
INVALID_STATUS_KEYWORDS = ("红冲", "作废")


def is_invoice_effective(inv: Invoice) -> bool:
    """该发票是否计入应收应付余额（已红冲/作废的不计）。"""
    st = (inv.status or "").strip()
    return not any(k in st for k in INVALID_STATUS_KEYWORDS)


# ------------------------------------------------------------------ 往来单位

def build_alias_index(db: Session) -> Dict[str, int]:
    """{归一化名称: counterparty_id}，含主名称与全部别名。"""
    idx: Dict[str, int] = {}
    for cid, name in db.query(Counterparty.id, Counterparty.name).all():
        k = normalize_name(name)
        if k and k not in idx:
            idx[k] = cid
    for cid, alias in db.query(CounterpartyAlias.counterparty_id,
                               CounterpartyAlias.alias).all():
        k = normalize_name(alias)
        if k and k not in idx:
            idx[k] = cid
    return idx


def resolve_counterparty(db: Session, name: Optional[str],
                         index: Optional[Dict[str, int]] = None) -> Optional[int]:
    """按名称（含别名）归属往来单位，匹配不到返回 None。"""
    if not name:
        return None
    idx = index if index is not None else build_alias_index(db)
    return idx.get(normalize_name(name))


def counterparty_key(obj) -> str:
    """对账分组键：有归属单位用 ID，否则退回归一化后的原始名称。"""
    if getattr(obj, "counterparty_id", None):
        return f"cp:{obj.counterparty_id}"
    return f"nm:{normalize_name(getattr(obj, 'counterparty_name_raw', None))}"


# ------------------------------------------------------------------ 余额

def recon_sums(db: Session) -> Tuple[Dict[int, Decimal], Dict[int, Decimal]]:
    """一次性算出所有发票与流水的已核销金额，避免逐行查询。

    发票侧已核销 = amount + diff_amount（差异金额也算结清，但钱没真收到/付出）；
    流水侧已核销 = amount（只认真实流动的钱）。
    """
    inv_map: Dict[int, Decimal] = {}
    txn_map: Dict[int, Decimal] = {}
    rows = db.query(
        Reconciliation.invoice_id, Reconciliation.txn_id,
        Reconciliation.amount, Reconciliation.diff_amount,
    ).filter(Reconciliation.status == "active").all()
    for iid, tid, amt, diff in rows:
        a = money(amt)
        inv_map[iid] = inv_map.get(iid, ZERO) + a + money(diff)
        txn_map[tid] = txn_map.get(tid, ZERO) + a
    return inv_map, txn_map


def recon_diff_sum(db: Session, invoice_id: Optional[int] = None,
                   recon_rows: Optional[list] = None) -> Decimal:
    """已确认的差异金额合计（抹零 / 手续费 / 汇损）。"""
    if recon_rows is None:
        q = db.query(Reconciliation.diff_amount).filter(
            Reconciliation.status == "active")
        if invoice_id is not None:
            q = q.filter(Reconciliation.invoice_id == invoice_id)
        recon_rows = q.all()
    return sum((money(r[0] if isinstance(r, tuple) else r) for r in recon_rows), ZERO)


def invoice_remaining(inv: Invoice, recon: Dict[int, Decimal]) -> Decimal:
    """发票未核销金额 = 价税合计 - 已核销。"""
    return money(inv.total) - money(recon.get(inv.id, ZERO))


def txn_remaining(txn: BankTxn, recon: Dict[int, Decimal]) -> Decimal:
    """流水未核销金额 = 金额 - 已核销。"""
    return money(txn.amount) - money(recon.get(txn.id, ZERO))


# ------------------------------------------------------------------ 过滤

def _as_date(v: Any) -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, date):
        return v
    if isinstance(v, datetime):
        return v.date()
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def apply_invoice_filters(q, p: Dict[str, Any]):
    """发票列表筛选。p 为请求参数字典。"""
    direction = p.get("direction")
    if direction:
        q = q.filter(Invoice.direction == direction)

    kw = (p.get("keyword") or "").strip()
    if kw:
        like = f"%{kw}%"
        q = q.filter(or_(
            Invoice.invoice_no.like(like),
            Invoice.seller_name.like(like),
            Invoice.buyer_name.like(like),
            Invoice.counterparty_name_raw.like(like),
        ))

    cp_id = p.get("counterparty_id")
    if cp_id:
        q = q.filter(Invoice.counterparty_id == int(cp_id))

    d0, d1 = _as_date(p.get("date_from")), _as_date(p.get("date_to"))
    if d0:
        q = q.filter(Invoice.invoice_date >= d0)
    if d1:
        q = q.filter(Invoice.invoice_date <= d1)

    amt0, amt1 = p.get("amount_min"), p.get("amount_max")
    if amt0 not in (None, ""):
        q = q.filter(Invoice.total >= money(amt0))
    if amt1 not in (None, ""):
        q = q.filter(Invoice.total <= money(amt1))

    status = p.get("status")
    if status == "open":          # 未核销完
        q = q.filter(Invoice.total > 0)
    elif status == "closed":
        q = q.filter(Invoice.total > 0)
    elif status == "invalid":     # 已红冲 / 作废
        conds = [Invoice.status.like(f"%{k}%") for k in INVALID_STATUS_KEYWORDS]
        q = q.filter(or_(*conds))
    elif status == "valid":
        for k in INVALID_STATUS_KEYWORDS:
            q = q.filter(~Invoice.status.like(f"%{k}%"))

    source = p.get("source")
    if source:
        q = q.filter(Invoice.source == source)
    return q


def apply_txn_filters(q, p: Dict[str, Any]):
    """流水列表筛选。"""
    bank = p.get("bank")
    if bank:
        q = q.filter(BankTxn.bank == bank)

    direction = p.get("direction")
    if direction:
        q = q.filter(BankTxn.direction == direction)

    kw = (p.get("keyword") or "").strip()
    if kw:
        like = f"%{kw}%"
        q = q.filter(or_(
            BankTxn.counterparty_name_raw.like(like),
            BankTxn.memo.like(like),
            BankTxn.purpose.like(like),
            BankTxn.counterparty_account.like(like),
        ))

    cp_id = p.get("counterparty_id")
    if cp_id:
        q = q.filter(BankTxn.counterparty_id == int(cp_id))

    d0, d1 = _as_date(p.get("date_from")), _as_date(p.get("date_to"))
    if d0:
        q = q.filter(BankTxn.txn_date >= d0)
    if d1:
        q = q.filter(BankTxn.txn_date <= d1)

    amt0, amt1 = p.get("amount_min"), p.get("amount_max")
    if amt0 not in (None, ""):
        q = q.filter(BankTxn.amount >= money(amt0))
    if amt1 not in (None, ""):
        q = q.filter(BankTxn.amount <= money(amt1))

    source = p.get("source")
    if source:
        q = q.filter(BankTxn.source == source)
    return q


# ------------------------------------------------------------------ 核销解除

def release_reconciliations(db: Session, invoice_id: Optional[int] = None,
                            txn_id: Optional[int] = None,
                            reason: str = "") -> List[dict]:
    """解除某张发票 / 某笔流水上的全部有效核销关系。

    删除已核销的单据前必须先解除，保证另一侧的未核销金额自动回滚。
    返回被解除记录的快照（写入操作日志，保证删除后仍可追溯）。
    """
    q = db.query(Reconciliation).filter(Reconciliation.status == "active")
    if invoice_id is not None:
        q = q.filter(Reconciliation.invoice_id == invoice_id)
    if txn_id is not None:
        q = q.filter(Reconciliation.txn_id == txn_id)
    rows = q.all()
    now = datetime.now()
    released = []
    for r in rows:
        released.append({
            "recon_id": r.id,
            "invoice_id": r.invoice_id,
            "txn_id": r.txn_id,
            "amount": str(r.amount),
            "method": r.method,
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else None,
            "created_by": r.created_by,
        })
        r.status = "reversed"
        r.reversed_at = now
        if reason:
            r.remark = f"{r.remark or ''} | 解除原因：{reason}".strip(" |")
    return released


# ------------------------------------------------------------------ 账龄

def aging_bucket(days: int) -> str:
    for label, lo, hi in AGING_BUCKETS:
        if lo <= days <= hi:
            return label
    return AGING_BUCKETS[-1][0]


def aging_labels() -> List[str]:
    return [b[0] for b in AGING_BUCKETS]
