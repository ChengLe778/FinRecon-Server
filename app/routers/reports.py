# -*- coding: utf-8 -*-
"""报表：余额表（可下钻）/ 账龄分析 / 月度趋势 / 欠款 TOP / 看板"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, extract, or_

from ..database import get_db, db_dialect
from ..deps import money
from ..models import Invoice, InvoiceItem, BankTxn, Counterparty, Reconciliation
from ..services import (
    recon_sums, invoice_remaining, is_invoice_effective,
    aging_bucket, aging_labels, counterparty_key,
)
from ..category import CATEGORIES, category_order
from ..response import ok, BizError

router = APIRouter(prefix="/api/reports", tags=["报表"])

ZERO = Decimal("0.00")


def _month_expr(col, alias: str = "ym"):
    """按月聚合表达式：SQLite 用 strftime，MySQL 用 date_format。

    返回值已带 label，group_by 要直接传这个返回值；
    不能写 group_by("ym") —— SQLAlchemy 2.0 无法解析文本标签引用，
    会抛 `Can't resolve label reference for ORDER BY / GROUP BY`。
    """
    if db_dialect() == "mysql":
        return func.date_format(col, "%Y-%m").label(alias)
    return func.strftime("%Y-%m", col).label(alias)

SCOPE = {
    "ar": {"invoice_direction": "销项", "txn_direction": "收", "label": "应收"},
    "ap": {"invoice_direction": "进项", "txn_direction": "支", "label": "应付"},
}


def _cp_label(obj) -> str:
    if getattr(obj, "counterparty", None):
        return obj.counterparty.name
    return obj.counterparty_name_raw or "(未认领)"


def _as_of(p: Optional[str]) -> date:
    if not p:
        return date.today()
    try:
        y, m, d = str(p)[:10].split("-")
        return date(int(y), int(m), int(d))
    except Exception:
        return date.today()


def _collect(db: Session, scope: str, counterparty_id: Optional[int] = None,
             date_from: Optional[str] = None, date_to: Optional[str] = None,
             keyword: Optional[str] = None):
    """取某一侧的发票与其未核销余额。"""
    cfg = SCOPE[scope]
    inv_recon, _ = recon_sums(db)
    q = db.query(Invoice).filter(Invoice.direction == cfg["invoice_direction"])
    if counterparty_id:
        q = q.filter(Invoice.counterparty_id == counterparty_id)
    if date_from:
        q = q.filter(Invoice.invoice_date >= date_from)
    if date_to:
        q = q.filter(Invoice.invoice_date <= date_to)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(Invoice.counterparty_name_raw.like(like))

    rows = []
    for inv in q.all():
        if not is_invoice_effective(inv):
            continue
        rem = invoice_remaining(inv, inv_recon)
        rows.append({
            "id": inv.id,
            "invoice_no": inv.invoice_no,
            "invoice_date": inv.invoice_date,
            "total": money(inv.total),
            "remaining": rem,
            "reconciled": money(inv.total) - rem,
            "counterparty_id": inv.counterparty_id,
            "counterparty": _cp_label(inv),
            "invoice_type": inv.invoice_type,
            "status": inv.status,
        })
    return rows


@router.get("/balance")
def balance(scope: str = "ar", counterparty_id: Optional[int] = None,
            date_from: Optional[str] = None, date_to: Optional[str] = None,
            keyword: Optional[str] = None,
            drill: Optional[int] = None,
            db: Session = Depends(get_db)):
    """应收/应付余额表。带 drill（往来单位 ID）时返回该单位的发票明细。"""
    if scope not in SCOPE:
        raise BizError("scope 只能是 ar / ap")
    rows = _collect(db, scope, counterparty_id, date_from, date_to, keyword)

    if drill:
        items = [r for r in rows if r["counterparty_id"] == drill]
        items.sort(key=lambda x: (x["invoice_date"] or date.min), reverse=False)
        return ok({
            "scope": scope, "label": SCOPE[scope]["label"], "drill": drill,
            "items": [{
                "invoice_id": r["id"],
                "invoice_no": r["invoice_no"],
                "invoice_date": r["invoice_date"].isoformat() if r["invoice_date"] else None,
                "total": str(r["total"]),
                "reconciled": str(r["reconciled"]),
                "remaining": str(r["remaining"]),
                "invoice_type": r["invoice_type"],
                "status": r["status"],
            } for r in items],
            "summary": {
                "count": len(items),
                "total": str(sum((r["total"] for r in items), ZERO)),
                "remaining": str(sum((r["remaining"] for r in items), ZERO)),
            },
        })

    groups: Dict[Any, Dict[str, Any]] = {}
    for r in rows:
        k = r["counterparty_id"] or f"raw:{r['counterparty']}"
        g = groups.setdefault(k, {
            "counterparty_id": r["counterparty_id"],
            "counterparty": r["counterparty"],
            "invoice_count": 0, "total": ZERO, "remaining": ZERO,
            "reconciled": ZERO,
            "earliest_date": None, "latest_date": None,
        })
        g["invoice_count"] += 1
        g["total"] += r["total"]
        g["remaining"] += r["remaining"]
        g["reconciled"] += r["reconciled"]
        d = r["invoice_date"]
        if d:
            g["earliest_date"] = min(g["earliest_date"], d) if g["earliest_date"] else d
            g["latest_date"] = max(g["latest_date"], d) if g["latest_date"] else d

    items = []
    for g in groups.values():
        items.append({
            "counterparty_id": g["counterparty_id"],
            "counterparty": g["counterparty"],
            "invoice_count": g["invoice_count"],
            "total": str(g["total"]),
            "reconciled": str(g["reconciled"]),
            "remaining": str(g["remaining"]),
            "earliest_date": g["earliest_date"].isoformat() if g["earliest_date"] else None,
            "latest_date": g["latest_date"].isoformat() if g["latest_date"] else None,
        })
    items.sort(key=lambda x: Decimal(x["remaining"]), reverse=True)

    return ok({
        "scope": scope, "label": SCOPE[scope]["label"],
        "items": items,
        "summary": {
            "counterparty_count": len(items),
            "invoice_count": sum(i["invoice_count"] for i in items),
            "total": str(sum((Decimal(i["total"]) for i in items), ZERO)),
            "remaining": str(sum((Decimal(i["remaining"]) for i in items), ZERO)),
        },
    })


@router.get("/aging")
def aging(scope: str = "ar", counterparty_id: Optional[int] = None,
          as_of: Optional[str] = None,
          date_from: Optional[str] = None, date_to: Optional[str] = None,
          db: Session = Depends(get_db)):
    """账龄分析：按发票日期分档（30/60/90/180/365+）。"""
    if scope not in SCOPE:
        raise BizError("scope 只能是 ar / ap")
    base = _as_of(as_of)
    rows = _collect(db, scope, counterparty_id, date_from, date_to)

    labels = aging_labels()
    buckets: Dict[str, Dict[str, Any]] = {
        lb: {"bucket": lb, "amount": ZERO, "count": 0} for lb in labels}

    by_cp: Dict[Any, Dict[str, Any]] = {}
    total = ZERO

    for r in rows:
        rem = r["remaining"]
        if rem <= 0:
            continue
        d = r["invoice_date"] or base
        days = (base - d).days
        lb = aging_bucket(max(days, 0))
        buckets[lb]["amount"] += rem
        buckets[lb]["count"] += 1
        total += rem

        g = by_cp.setdefault(r["counterparty_id"] or f"raw:{r['counterparty']}", {
            "counterparty_id": r["counterparty_id"],
            "counterparty": r["counterparty"],
            "total": ZERO,
            **{lb: ZERO for lb in labels},
        })
        g["total"] += rem
        g[lb] += rem

    by_cp_list = sorted(by_cp.values(),
                        key=lambda x: x["total"], reverse=True)
    for g in by_cp_list:
        g["total"] = str(g["total"])
        for lb in labels:
            g[lb] = str(g[lb])

    return ok({
        "scope": scope, "label": SCOPE[scope]["label"],
        "as_of": base.isoformat(),
        "buckets": [{
            "bucket": b["bucket"],
            "count": b["count"],
            "amount": str(b["amount"]),
            "ratio": (float(b["amount"] / total * 100) if total > 0 else 0.0),
        } for b in buckets.values()],
        "by_counterparty": by_cp_list,
        "summary": {
            "total": str(total),
            "overdue_90": str(sum((buckets[lb]["amount"] for lb in labels
                                   if lb not in ("0-30", "31-60", "61-90")), ZERO)),
            "invoice_count": sum(b["count"] for b in buckets.values()),
        },
    })


@router.get("/trend")
def trend(months: int = 24, db: Session = Depends(get_db)):
    """月度收付款趋势（按银行流水）。"""
    mon = _month_expr(BankTxn.txn_date)
    rows = (db.query(
        mon,
        BankTxn.direction,
        func.sum(BankTxn.amount),
    ).group_by(mon, BankTxn.direction).all())

    data: Dict[str, Dict[str, Decimal]] = defaultdict(
        lambda: {"收": ZERO, "支": ZERO})
    for ym, direction, amt in rows:
        if not ym:
            continue
        data[ym][direction] += money(amt)

    keys = sorted(data.keys())[-months:] if months > 0 else sorted(data.keys())
    items = [{"month": k, "income": str(data[k]["收"]),
              "expense": str(data[k]["支"]),
              "net": str(data[k]["收"] - data[k]["支"])} for k in keys]
    return ok({"items": items})


@router.get("/top")
def top(scope: str = "ar", limit: int = 10, as_of: Optional[str] = None,
        db: Session = Depends(get_db)):
    """欠款 TOP 榜：按未核销余额排序，标注最长账龄。"""
    if scope not in SCOPE:
        raise BizError("scope 只能是 ar / ap")
    base = _as_of(as_of)
    rows = _collect(db, scope)

    groups: Dict[Any, Dict[str, Any]] = {}
    for r in rows:
        rem = r["remaining"]
        if rem <= 0:
            continue
        k = r["counterparty_id"] or f"raw:{r['counterparty']}"
        g = groups.setdefault(k, {
            "counterparty_id": r["counterparty_id"],
            "counterparty": r["counterparty"],
            "remaining": ZERO, "count": 0, "oldest_days": 0,
            "oldest_date": None,
        })
        g["remaining"] += rem
        g["count"] += 1
        d = r["invoice_date"] or base
        days = max((base - d).days, 0)
        if days > g["oldest_days"]:
            g["oldest_days"] = days
            g["oldest_date"] = d

    items = sorted(groups.values(), key=lambda x: x["remaining"], reverse=True)[:limit]
    for g in items:
        g["remaining"] = str(g["remaining"])
        g["oldest_date"] = g["oldest_date"].isoformat() if g["oldest_date"] else None
        g["aging_bucket"] = aging_bucket(g["oldest_days"])
        g["risk"] = ("高" if g["oldest_days"] > 180 else
                     "中" if g["oldest_days"] > 90 else "低")

    return ok({"scope": scope, "label": SCOPE[scope]["label"],
               "as_of": base.isoformat(), "items": items})


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    """总览看板：关键卡片 + 趋势 + TOP 榜 + 账龄概览。"""
    inv_recon, txn_recon = recon_sums(db)
    today = date.today()
    month_start = today.replace(day=1)

    out: Dict[str, Any] = {"as_of": today.isoformat()}

    for scope, cfg in SCOPE.items():
        invs = [i for i in db.query(Invoice).filter(
            Invoice.direction == cfg["invoice_direction"]).all()
            if is_invoice_effective(i)]
        rem = sum((invoice_remaining(i, inv_recon) for i in invs), ZERO)
        total = sum((money(i.total) for i in invs), ZERO)
        overdue = ZERO
        for i in invs:
            r = invoice_remaining(i, inv_recon)
            if r > 0 and i.invoice_date:
                if (today - i.invoice_date).days > 90:
                    overdue += r
        out[scope] = {
            "label": cfg["label"],
            "balance": str(rem),
            "total": str(total),
            "reconciled": str(total - rem),
            "invoice_count": len(invs),
            "open_count": sum(1 for i in invs
                              if invoice_remaining(i, inv_recon) > 0
                              and money(i.total) > 0),
            "overdue_90": str(overdue),
        }

    # 本月 / 上月 收付款
    def _sum(dir_: str, d0: date, d1: date) -> Decimal:
        v = db.query(func.coalesce(func.sum(BankTxn.amount), 0)).filter(
            BankTxn.direction == dir_,
            BankTxn.txn_date >= d0, BankTxn.txn_date < d1).scalar()
        return money(v)

    last_month_start = (month_start - timedelta(days=1)).replace(day=1)
    out["cash"] = {
        "month_income": str(_sum("收", month_start, today + timedelta(days=1))),
        "month_expense": str(_sum("支", month_start, today + timedelta(days=1))),
        "last_month_income": str(_sum("收", last_month_start, month_start)),
        "last_month_expense": str(_sum("支", last_month_start, month_start)),
    }

    # 数据量概览
    out["counts"] = {
        "invoice": db.query(func.count(Invoice.id)).scalar() or 0,
        "txn": db.query(func.count(BankTxn.id)).scalar() or 0,
        "counterparty": db.query(func.count(Counterparty.id)).scalar() or 0,
        "recon_active": db.query(func.count(Reconciliation.id)).filter(
            Reconciliation.status == "active").scalar() or 0,
    }

    # 未认领名称数
    out["unclaimed"] = {
        "txn": db.query(func.count(BankTxn.id)).filter(
            BankTxn.counterparty_id.is_(None)).scalar() or 0,
        "invoice": db.query(func.count(Invoice.id)).filter(
            Invoice.counterparty_id.is_(None)).scalar() or 0,
    }
    return ok(out)


# ------------------------------------------------------------------ 费用分析

@router.get("/expense")
def expense(direction: str = "进项",
            date_from: Optional[str] = None,
            date_to: Optional[str] = None,
            category: Optional[str] = None,
            keyword: Optional[str] = None,
            drill: Optional[str] = None,
            page: int = 1, size: int = 20,
            db: Session = Depends(get_db)):
    """费用结构分析：餐饮 / 油费 / 住宿 / 材料… 一图看清钱花在哪。

    数据来源是发票明细行（invoice_item），按税收分类大类 + 关键词归类。
    drill=某类别 时返回该类别下的明细行（分页）。
    """
    if direction not in ("销项", "进项"):
        raise BizError("direction 只能是 销项 / 进项")

    def _base():
        q = db.query(InvoiceItem).join(Invoice, Invoice.id == InvoiceItem.invoice_id)
        q = q.filter(Invoice.direction == direction)
        if date_from:
            q = q.filter(Invoice.invoice_date >= date_from)
        if date_to:
            q = q.filter(Invoice.invoice_date <= date_to)
        if keyword:
            like = f"%{keyword.strip()}%"
            q = q.filter(or_(InvoiceItem.name.like(like),
                             Invoice.seller_name.like(like),
                             Invoice.buyer_name.like(like)))
        return q

    # ---------------- 下钻：某类别的明细行 ----------------
    if drill:
        q = _base().filter(InvoiceItem.category == drill)
        total = q.count()
        rows = (q.order_by(Invoice.invoice_date.desc(), InvoiceItem.id.desc())
                .offset(max(0, (page - 1) * size)).limit(size).all())
        return ok({
            "drill": drill, "total": total, "page": page, "size": size,
            "items": [{
                "invoice_id": it.invoice_id,
                "invoice_no": it.invoice.invoice_no,
                "invoice_date": (it.invoice.invoice_date.isoformat()
                                 if it.invoice.invoice_date else None),
                "seller": it.invoice.seller_name if direction == "进项" else it.invoice.buyer_name,
                "name": it.name,
                "category": it.category,
                "qty": str(it.qty) if it.qty is not None else None,
                "unit": it.unit,
                "amount": str(money(it.amount)),
                "tax": str(money(it.tax)),
                "total": str(money(it.total)),
            } for it in rows],
        })

    # ---------------- 按类别汇总 ----------------
    q = _base()
    if category:
        q = q.filter(InvoiceItem.category == category)

    cat_rows = (db.query(
        InvoiceItem.category,
        func.count(func.distinct(InvoiceItem.invoice_id)),
        func.count(InvoiceItem.id),
        func.coalesce(func.sum(InvoiceItem.amount), 0),
        func.coalesce(func.sum(InvoiceItem.tax), 0),
        func.coalesce(func.sum(InvoiceItem.total), 0),
    ).join(Invoice, Invoice.id == InvoiceItem.invoice_id)
        .filter(Invoice.direction == direction))
    if date_from:
        cat_rows = cat_rows.filter(Invoice.invoice_date >= date_from)
    if date_to:
        cat_rows = cat_rows.filter(Invoice.invoice_date <= date_to)
    if keyword:
        like = f"%{keyword.strip()}%"
        cat_rows = cat_rows.filter(or_(InvoiceItem.name.like(like),
                                       Invoice.seller_name.like(like),
                                       Invoice.buyer_name.like(like)))
    cat_rows = cat_rows.group_by(InvoiceItem.category).all()

    items = [{
        "category": c or "其他",
        "invoice_count": int(ic or 0),
        "item_count": int(itc or 0),
        "amount": str(money(amt)),
        "tax": str(money(tax)),
        "total": str(money(tot)),
    } for c, ic, itc, amt, tax, tot in cat_rows]
    grand = sum((Decimal(x["total"]) for x in items), ZERO)
    for x in items:
        x["ratio"] = round(float(Decimal(x["total"]) / grand * 100), 2) if grand > 0 else 0.0
    items.sort(key=lambda x: -Decimal(x["total"]))

    # ---------------- 月度趋势（类别 × 月份）----------------
    mon = _month_expr(Invoice.invoice_date)
    mon_rows = (db.query(
        mon,
        InvoiceItem.category,
        func.coalesce(func.sum(InvoiceItem.total), 0),
    ).join(Invoice, Invoice.id == InvoiceItem.invoice_id)
        .filter(Invoice.direction == direction))
    if date_from:
        mon_rows = mon_rows.filter(Invoice.invoice_date >= date_from)
    if date_to:
        mon_rows = mon_rows.filter(Invoice.invoice_date <= date_to)
    if keyword:
        like = f"%{keyword.strip()}%"
        mon_rows = mon_rows.filter(or_(InvoiceItem.name.like(like),
                                       Invoice.seller_name.like(like),
                                       Invoice.buyer_name.like(like)))
    mon_rows = mon_rows.group_by(mon, InvoiceItem.category).all()

    by_month: Dict[str, Dict[str, Any]] = {}
    for ym, c, tot in mon_rows:
        if not ym:
            continue
        g = by_month.setdefault(ym, {"month": ym, "total": ZERO})
        g[c or "其他"] = g.get(c or "其他", ZERO) + money(tot)
        g["total"] += money(tot)
    months = sorted(by_month.values(), key=lambda x: x["month"])
    for g in months:
        g["total"] = str(g["total"])
        for k, v in list(g.items()):
            if k not in ("month", "total"):
                g[k] = str(v)

    # ---------------- 供应商 / 客户 TOP ----------------
    sup_rows = (db.query(
        Invoice.counterparty_name_raw,
        InvoiceItem.category,
        func.coalesce(func.sum(InvoiceItem.total), 0),
    ).join(Invoice, Invoice.id == InvoiceItem.invoice_id)
        .filter(Invoice.direction == direction))
    if date_from:
        sup_rows = sup_rows.filter(Invoice.invoice_date >= date_from)
    if date_to:
        sup_rows = sup_rows.filter(Invoice.invoice_date <= date_to)
    sup_rows = sup_rows.group_by(Invoice.counterparty_name_raw,
                                 InvoiceItem.category).all()
    sup_map: Dict[str, Dict[str, Any]] = {}
    for nm, c, tot in sup_rows:
        g = sup_map.setdefault(nm or "(未认领)", {"name": nm or "(未认领)", "total": ZERO})
        g["total"] += money(tot)
        g[c or "其他"] = g.get(c or "其他", ZERO) + money(tot)
    suppliers = sorted(sup_map.values(), key=lambda x: -x["total"])[:20]
    for g in suppliers:
        g["total"] = str(g["total"])
        for k, v in list(g.items()):
            if k != "name":
                g[k] = str(v)

    # ---------------- 无明细发票（未分类）统计 ----------------
    no_item_q = db.query(func.count(Invoice.id)).filter(
        Invoice.direction == direction,
        ~Invoice.items.any(),
    )
    if date_from:
        no_item_q = no_item_q.filter(Invoice.invoice_date >= date_from)
    if date_to:
        no_item_q = no_item_q.filter(Invoice.invoice_date <= date_to)
    no_item_count = no_item_q.scalar() or 0

    return ok({
        "direction": direction,
        "items": items,
        "months": months,
        "suppliers": suppliers,
        "categories": CATEGORIES,
        "summary": {
            "total": str(grand),
            "invoice_count": int(db.query(func.count(func.distinct(InvoiceItem.invoice_id)))
                                 .join(Invoice, Invoice.id == InvoiceItem.invoice_id)
                                 .filter(Invoice.direction == direction).scalar() or 0),
            "item_count": sum(x["item_count"] for x in items),
            "amount": str(sum((Decimal(x["amount"]) for x in items), ZERO)),
            "tax": str(sum((Decimal(x["tax"]) for x in items), ZERO)),
            "category_count": len(items),
            "uncategorized_invoice_count": no_item_count,
        },
    })
