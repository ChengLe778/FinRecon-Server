# -*- coding: utf-8 -*-
"""报表与原始数据导出：xlsx（多 sheet）/ csv。

导出内容 = 当前页面所见（受筛选条件影响），文件名带日期与报表类型。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_
from decimal import Decimal

from ..database import get_db
from ..deps import money
from ..models import Invoice, InvoiceItem, BankTxn, Reconciliation
from ..services import (
    apply_invoice_filters, apply_txn_filters, recon_sums, invoice_remaining,
    txn_remaining, is_invoice_effective, aging_bucket, aging_labels,
)
from ..export import build_xlsx, build_csv
from ..response import BizError

router = APIRouter(prefix="/api/export", tags=["导出"])

ZERO = Decimal("0.00")
MAX_ROWS = 60000


def _resp(buf, filename: str, fmt: str):
    media = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
             if fmt == "xlsx" else "text/csv; charset=utf-8")
    # 中文文件名：ASCII 回退名 + RFC 5987 UTF-8 名
    return StreamingResponse(
        buf,
        media_type=media,
        headers={"Content-Disposition":
                 f"attachment; filename=export.{fmt}; filename*=UTF-8''{quote(filename)}"},
    )


def _stamp() -> str:
    return date.today().strftime("%Y%m%d")


def _summary_text(params: Dict[str, Any], keys: List[str],
                  labels: Dict[str, str]) -> str:
    parts = []
    for k in keys:
        v = params.get(k)
        if v not in (None, ""):
            parts.append(f"{labels.get(k, k)}{v}")
    return "_".join(parts) if parts else "全部"


# --------------------------------------------------------------- 余额表

@router.get("/balance")
def export_balance(scope: str = "ar", fmt: str = "xlsx",
                   counterparty_id: Optional[int] = None,
                   date_from: Optional[str] = None,
                   date_to: Optional[str] = None,
                   keyword: Optional[str] = None,
                   as_of: Optional[str] = None,
                   db: Session = Depends(get_db)):
    from .reports import _collect, SCOPE
    if scope not in SCOPE:
        raise BizError("scope 只能是 ar / ap")

    rows = _collect(db, scope, counterparty_id, date_from, date_to, keyword)
    label = SCOPE[scope]["label"]
    base = date.today()
    if as_of:
        try:
            y, m, d = str(as_of)[:10].split("-")
            base = date(int(y), int(m), int(d))
        except Exception:
            pass

    groups: Dict[Any, Dict[str, Any]] = {}
    for r in rows:
        k = r["counterparty_id"] or f"raw:{r['counterparty']}"
        g = groups.setdefault(k, {
            "counterparty": r["counterparty"], "count": 0,
            "total": ZERO, "remaining": ZERO, "reconciled": ZERO,
        })
        g["count"] += 1
        g["total"] += r["total"]
        g["remaining"] += r["remaining"]
        g["reconciled"] += r["reconciled"]

    summary_rows = sorted(groups.values(),
                          key=lambda x: x["remaining"], reverse=True)
    s_rows = [[g["counterparty"], g["count"], float(g["total"]),
               float(g["reconciled"]), float(g["remaining"])]
              for g in summary_rows]
    s_total = ["合计", sum(g["count"] for g in summary_rows),
               float(sum((g["total"] for g in summary_rows), ZERO)),
               float(sum((g["reconciled"] for g in summary_rows), ZERO)),
               float(sum((g["remaining"] for g in summary_rows), ZERO))]

    detail_rows = []
    for r in sorted(rows, key=lambda x: (x["invoice_date"] or date.min)):
        d = r["invoice_date"]
        days = (base - d).days if d else 0
        detail_rows.append([
            r["counterparty"], r["invoice_no"] or "",
            d.isoformat() if d else "",
            max(days, 0), aging_bucket(max(days, 0)),
            float(r["total"]), float(r["reconciled"]), float(r["remaining"]),
            r["status"] or "", r["invoice_type"] or "",
        ])
    d_total = ["合计", "", "", "", "",
               float(sum((r["total"] for r in rows), ZERO)),
               float(sum((r["reconciled"] for r in rows), ZERO)),
               float(sum((r["remaining"] for r in rows), ZERO)), "", ""]

    stamp = _stamp()
    fname = f"{label}余额表_{_summary_text(locals().get('params') or {}, [], {})}_{stamp}"

    if fmt == "csv":
        buf = build_csv(
            ["往来单位", "发票张数", "开票金额", "已核销", "未核销余额"], s_rows)
        return _resp(buf, f"{fname}.csv", "csv")

    buf = build_xlsx([
        {"name": f"{label}分单位汇总",
         "headers": ["往来单位", "发票张数", "开票金额", "已核销", "未核销余额"],
         "rows": s_rows, "money_cols": (3, 4, 5), "total_row": s_total},
        {"name": "发票明细",
         "headers": ["往来单位", "发票号码", "开票日期", "账龄天数", "账龄区间",
                     "价税合计", "已核销", "未核销余额", "状态", "票种"],
         "rows": detail_rows, "money_cols": (6, 7, 8), "total_row": d_total},
    ])
    return _resp(buf, f"{fname}.xlsx", "xlsx")


# --------------------------------------------------------------- 账龄

@router.get("/aging")
def export_aging(scope: str = "ar", fmt: str = "xlsx",
                 counterparty_id: Optional[int] = None,
                 as_of: Optional[str] = None,
                 db: Session = Depends(get_db)):
    from .reports import _collect, SCOPE
    if scope not in SCOPE:
        raise BizError("scope 只能是 ar / ap")

    base = date.today()
    if as_of:
        try:
            y, m, d = str(as_of)[:10].split("-")
            base = date(int(y), int(m), int(d))
        except Exception:
            pass

    rows = _collect(db, scope, counterparty_id)
    labels = aging_labels()
    by_cp: Dict[Any, Dict[str, Any]] = {}
    for r in rows:
        rem = r["remaining"]
        if rem <= 0:
            continue
        k = r["counterparty_id"] or f"raw:{r['counterparty']}"
        g = by_cp.setdefault(k, {"counterparty": r["counterparty"],
                                 "total": ZERO, **{lb: ZERO for lb in labels}})
        days = max((base - (r["invoice_date"] or base)).days, 0)
        g[aging_bucket(days)] += rem
        g["total"] += rem

    items = sorted(by_cp.values(), key=lambda x: x["total"], reverse=True)
    a_rows = [[g["counterparty"]] + [float(g[lb]) for lb in labels] +
              [float(g["total"])] for g in items]
    a_total = ["合计"] + [
        float(sum((g[lb] for g in items), ZERO)) for lb in labels] + [
        float(sum((g["total"] for g in items), ZERO))]

    detail = []
    for r in sorted(rows, key=lambda x: (x["invoice_date"] or date.min)):
        if r["remaining"] <= 0:
            continue
        d = r["invoice_date"]
        days = max((base - d).days if d else 0, 0)
        detail.append([r["counterparty"], r["invoice_no"] or "",
                       d.isoformat() if d else "", days,
                       aging_bucket(days), float(r["total"]),
                       float(r["remaining"])])
    d_total = ["合计", "", "", "", "",
               float(sum((r["total"] for r in rows if r["remaining"] > 0), ZERO)),
               float(sum((r["remaining"] for r in rows if r["remaining"] > 0), ZERO))]

    label = SCOPE[scope]["label"]
    fname = f"{label}账龄分析_{base.strftime('%Y%m%d')}"

    if fmt == "csv":
        buf = build_csv(["往来单位"] + labels + ["合计"], a_rows)
        return _resp(buf, f"{fname}.csv", "csv")

    buf = build_xlsx([
        {"name": "账龄分单位汇总",
         "headers": ["往来单位"] + labels + ["合计"],
         "rows": a_rows,
         "money_cols": tuple(range(2, len(labels) + 3)),
         "total_row": a_total},
        {"name": "未核销发票明细",
         "headers": ["往来单位", "发票号码", "开票日期", "账龄天数", "账龄区间",
                     "价税合计", "未核销余额"],
         "rows": detail, "money_cols": (6, 7), "total_row": d_total},
    ])
    return _resp(buf, f"{fname}.xlsx", "xlsx")


# --------------------------------------------------------------- 原始数据

@router.get("/invoices")
def export_invoices(fmt: str = "xlsx",
                    direction: Optional[str] = None,
                    keyword: Optional[str] = None,
                    counterparty_id: Optional[int] = None,
                    date_from: Optional[str] = None,
                    date_to: Optional[str] = None,
                    status: Optional[str] = None,
                    source: Optional[str] = None,
                    db: Session = Depends(get_db)):
    params = dict(direction=direction, keyword=keyword,
                  counterparty_id=counterparty_id, date_from=date_from,
                  date_to=date_to, status=status, source=source)
    q = apply_invoice_filters(db.query(Invoice), params)
    rows = q.order_by(Invoice.invoice_date.desc()).all()
    if len(rows) > MAX_ROWS:
        raise BizError(f"结果共 {len(rows)} 行，超过单次导出上限 {MAX_ROWS}，请缩小筛选范围分批导出")

    inv_recon, _ = recon_sums(db)
    body = []
    for i in rows:
        rem = invoice_remaining(i, inv_recon)
        body.append([
            i.direction, i.invoice_no or "",
            i.invoice_date.isoformat() if i.invoice_date else "",
            (i.counterparty.name if i.counterparty else i.counterparty_name_raw) or "",
            i.seller_name or "", i.buyer_name or "",
            float(money(i.amount)), float(money(i.tax)), float(money(i.total)),
            float(money(i.total) - rem), float(rem),
            i.status or "", i.invoice_type or "", i.item_count,
            "是" if is_invoice_effective(i) else "否",
            i.source,
        ])
    headers = ["方向", "发票号码", "开票日期", "往来单位", "销方名称", "购方名称",
               "金额", "税额", "价税合计", "已核销", "未核销余额",
               "状态", "票种", "明细行数", "是否计入余额", "来源"]
    total = ["合计", "", "", "", "", "",
             float(sum((money(i.amount) for i in rows), ZERO)),
             float(sum((money(i.tax) for i in rows), ZERO)),
             float(sum((money(i.total) for i in rows), ZERO)),
             float(sum((money(i.total) - invoice_remaining(i, inv_recon)
                        for i in rows), ZERO)),
             float(sum((invoice_remaining(i, inv_recon) for i in rows), ZERO)),
             "", "", "", "", ""]

    fname = f"发票明细_{_summary_text(params, ['direction', 'date_from', 'date_to', 'status'],
                                      {'direction': '', 'date_from': '从', 'date_to': '到',
                                       'status': ''})}_{_stamp()}"
    if fmt == "csv":
        return _resp(build_csv(headers, body), f"{fname}.csv", "csv")
    buf = build_xlsx([{"name": "发票明细", "headers": headers, "rows": body,
                       "money_cols": (7, 8, 9, 10, 11), "total_row": total}])
    return _resp(buf, f"{fname}.xlsx", "xlsx")


@router.get("/txns")
def export_txns(fmt: str = "xlsx",
                bank: Optional[str] = None,
                direction: Optional[str] = None,
                keyword: Optional[str] = None,
                counterparty_id: Optional[int] = None,
                date_from: Optional[str] = None,
                date_to: Optional[str] = None,
                source: Optional[str] = None,
                db: Session = Depends(get_db)):
    params = dict(bank=bank, direction=direction, keyword=keyword,
                  counterparty_id=counterparty_id, date_from=date_from,
                  date_to=date_to, source=source)
    q = apply_txn_filters(db.query(BankTxn), params)
    rows = q.order_by(BankTxn.txn_date.desc()).all()
    if len(rows) > MAX_ROWS:
        raise BizError(f"结果共 {len(rows)} 行，超过单次导出上限 {MAX_ROWS}，请缩小筛选范围分批导出")

    _, txn_recon = recon_sums(db)
    body = []
    for t in rows:
        rem = txn_remaining(t, txn_recon)
        body.append([
            t.bank, t.account_no or "",
            t.txn_date.isoformat() if t.txn_date else "",
            t.txn_time or "", t.direction,
            float(money(t.amount)), float(money(t.amount) - rem), float(rem),
            float(money(t.balance)) if t.balance is not None else None,
            (t.counterparty.name if t.counterparty else t.counterparty_name_raw) or "",
            t.counterparty_account or "", t.memo or "", t.purpose or "",
            t.serial_no or "", t.source,
        ])
    headers = ["银行", "账号", "交易日期", "交易时间", "方向",
               "金额", "已核销", "未核销余额", "账户余额",
               "对方名称", "对方账号", "摘要", "用途", "流水号", "来源"]
    total = ["合计", "", "", "", "",
             float(sum((money(t.amount) for t in rows), ZERO)),
             float(sum((money(t.amount) - txn_remaining(t, txn_recon)
                        for t in rows), ZERO)),
             float(sum((txn_remaining(t, txn_recon) for t in rows), ZERO)),
             "", "", "", "", "", "", ""]

    fname = f"银行流水_{_summary_text(params, ['bank', 'direction', 'date_from', 'date_to'],
                                      {'bank': '', 'direction': '', 'date_from': '从',
                                       'date_to': '到'})}_{_stamp()}"
    if fmt == "csv":
        return _resp(build_csv(headers, body), f"{fname}.csv", "csv")
    buf = build_xlsx([{"name": "银行流水", "headers": headers, "rows": body,
                       "money_cols": (6, 7, 8, 9), "total_row": total}])
    return _resp(buf, f"{fname}.xlsx", "xlsx")


# --------------------------------------------------------------- 核销记录

@router.get("/reconcile")
def export_reconcile(fmt: str = "xlsx", status: Optional[str] = None,
                     method: Optional[str] = None,
                     db: Session = Depends(get_db)):
    q = db.query(Reconciliation, Invoice, BankTxn).join(
        Invoice, Invoice.id == Reconciliation.invoice_id).join(
        BankTxn, BankTxn.id == Reconciliation.txn_id)
    if status:
        q = q.filter(Reconciliation.status == status)
    if method:
        q = q.filter(Reconciliation.method == method)
    rows = q.order_by(Reconciliation.created_at.desc()).all()

    body = [[
        r.id,
        "有效" if r.status == "active" else "已撤销",
        "自动" if r.method == "auto" else "手工",
        i.direction, i.invoice_no or "",
        i.invoice_date.isoformat() if i.invoice_date else "",
        (i.counterparty.name if i.counterparty else i.counterparty_name_raw) or "",
        float(money(i.total)),
        t.bank, t.txn_date.isoformat() if t.txn_date else "",
        (t.counterparty.name if t.counterparty else t.counterparty_name_raw) or "",
        t.direction, float(money(t.amount)),
        float(money(r.amount)),
        r.created_by or "",
        r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
        r.reversed_at.strftime("%Y-%m-%d %H:%M") if r.reversed_at else "",
        r.remark or "",
    ] for r, i, t in rows]
    headers = ["核销ID", "状态", "方式", "发票方向", "发票号码", "开票日期",
               "发票往来单位", "发票价税合计", "银行", "交易日期", "流水对方",
               "流水方向", "流水金额", "本次核销金额", "操作人",
               "核销时间", "撤销时间", "备注"]
    total = ["合计", "", "", "", "", "", "", "", "", "", "", "",
             float(sum((money(t.amount) for _, _, t in rows), ZERO)),
             float(sum((money(r.amount) for r, _, _ in rows
                        if r.status == "active"), ZERO)), "", "", "", ""]

    fname = f"核销记录_{_stamp()}"
    if fmt == "csv":
        return _resp(build_csv(headers, body), f"{fname}.csv", "csv")
    buf = build_xlsx([{"name": "核销记录", "headers": headers, "rows": body,
                       "money_cols": (8, 13, 14), "total_row": total}])
    return _resp(buf, f"{fname}.xlsx", "xlsx")


# --------------------------------------------------------------- 费用分析

@router.get("/expense")
def export_expense(fmt: str = "xlsx",
                   direction: str = "进项",
                   date_from: Optional[str] = None,
                   date_to: Optional[str] = None,
                   keyword: Optional[str] = None,
                   category: Optional[str] = None,
                   with_detail: bool = True,
                   db: Session = Depends(get_db)):
    """费用结构导出：餐饮 / 油费 / 住宿 … 一表看清钱花在哪。

    xlsx 三个 sheet：类别汇总、月度×类别、明细行；
    csv 只导出明细行（csv 无多 sheet）。
    """
    if direction not in ("销项", "进项"):
        raise BizError("direction 只能是 销项 / 进项")

    from .reports import _month_expr
    from ..category import category_order

    def _base():
        q = (db.query(InvoiceItem, Invoice)
             .join(Invoice, Invoice.id == InvoiceItem.invoice_id)
             .filter(Invoice.direction == direction))
        if date_from:
            q = q.filter(Invoice.invoice_date >= date_from)
        if date_to:
            q = q.filter(Invoice.invoice_date <= date_to)
        if keyword:
            like = f"%{keyword.strip()}%"
            q = q.filter(or_(InvoiceItem.name.like(like),
                             Invoice.seller_name.like(like),
                             Invoice.buyer_name.like(like)))
        if category:
            q = q.filter(InvoiceItem.category == category)
        return q

    pairs = _base().order_by(Invoice.invoice_date.desc(),
                             InvoiceItem.id.desc()).all()
    if len(pairs) > MAX_ROWS:
        raise BizError(f"明细共 {len(pairs)} 行，超过单次导出上限 {MAX_ROWS}，"
                       f"请缩小筛选范围或用 with_detail=false 只导汇总")

    # ---------------- 类别汇总 ----------------
    agg: Dict[str, Dict[str, Any]] = {}
    months_set: List[str] = []
    mon_map: Dict[Any, Dict[str, Any]] = {}
    for it, inv in pairs:
        cat = it.category or "其他"
        g = agg.setdefault(cat, {"cat": cat, "invs": set(), "cnt": 0,
                                 "amt": ZERO, "tax": ZERO, "tot": ZERO})
        g["invs"].add(it.invoice_id)
        g["cnt"] += 1
        g["amt"] += money(it.amount)
        g["tax"] += money(it.tax)
        g["tot"] += money(it.total)

        ym = inv.invoice_date.strftime("%Y-%m") if inv.invoice_date else ""
        if ym:
            mg = mon_map.setdefault(ym, {})
            mg[cat] = mg.get(cat, ZERO) + money(it.total)

    grand = sum((g["tot"] for g in agg.values()), ZERO)
    cat_rows = []
    for g in sorted(agg.values(), key=lambda x: -x["tot"]):
        cat_rows.append([
            g["cat"], len(g["invs"]), g["cnt"],
            float(g["amt"]), float(g["tax"]), float(g["tot"]),
            (float(g["tot"] / grand * 100) if grand > 0 else 0.0),
        ])
    cat_rows.sort(key=lambda r: (-r[5], category_order(r[0])))
    cat_total = ["合计", "", sum(r[2] for r in cat_rows),
                 float(sum((Decimal(str(r[3])) for r in cat_rows), ZERO)),
                 float(sum((Decimal(str(r[4])) for r in cat_rows), ZERO)),
                 float(grand), 100.0]

    # ---------------- 月度 × 类别 ----------------
    months_set = sorted(mon_map.keys())
    all_cats = sorted({g["cat"] for g in agg.values()},
                      key=lambda c: (-agg[c]["tot"], category_order(c)))
    mon_rows = []
    for c in all_cats:
        line = [c]
        s = ZERO
        for m in months_set:
            v = mon_map.get(m, {}).get(c, ZERO)
            s += v
            line.append(float(v))
        line.append(float(s))
        mon_rows.append(line)
    mon_total = ["合计"] + [float(sum((mon_map.get(m, {}).get(c, ZERO)
                                       for c in all_cats), ZERO))
                            for m in months_set]
    mon_total.append(float(sum((Decimal(str(x)) for x in mon_total[1:]), ZERO)))

    # ---------------- 明细行 ----------------
    headers = (["开票日期", "发票号码", "往来单位",
                "销方名称" if direction == "进项" else "购方名称",
                "货物或应税劳务名称", "费用类别", "税收分类编码",
                "规格型号", "单位", "数量", "单价", "不含税金额", "税率",
                "税额", "价税合计"])
    detail = [[
        inv.invoice_date.isoformat() if inv.invoice_date else "",
        inv.invoice_no or "",
        (inv.counterparty.name if inv.counterparty
         else inv.counterparty_name_raw) or "",
        (inv.seller_name if direction == "进项" else inv.buyer_name) or "",
        it.name or "", it.category or "其他", it.tax_code or "",
        it.spec or "", it.unit or "",
        float(it.qty) if it.qty is not None else None,
        float(it.price) if it.price is not None else None,
        float(money(it.amount)), it.tax_rate or "",
        float(money(it.tax)), float(money(it.total)),
    ] for it, inv in pairs]
    d_total = ["合计", "", "", "", "", "", "", "", "", "", "",
               float(sum((money(it.amount) for it, _ in pairs), ZERO)), "",
               float(sum((money(it.tax) for it, _ in pairs), ZERO)),
               float(sum((money(it.total) for it, _ in pairs), ZERO))]

    params = dict(direction=direction, date_from=date_from, date_to=date_to,
                  category=category, keyword=keyword)
    fname = f"费用分析_{_summary_text(params, ['direction', 'date_from', 'date_to', 'category'],
                                      {'direction': '', 'date_from': '从',
                                       'date_to': '到', 'category': ''})}_{_stamp()}"

    if fmt == "csv":
        return _resp(build_csv(headers, detail), f"{fname}_明细.csv", "csv")

    sheets = [
        {"name": "费用类别汇总",
         "headers": ["费用类别", "发票张数", "明细行数", "不含税金额", "税额",
                     "价税合计", "占比%"],
         "rows": cat_rows, "money_cols": (4, 5, 6), "total_row": cat_total},
        {"name": "月度趋势",
         "headers": ["费用类别"] + months_set + ["合计"],
         "rows": mon_rows,
         "money_cols": tuple(range(1, len(months_set) + 2)),
         "total_row": mon_total},
    ]
    if with_detail:
        sheets.append({"name": "明细行", "headers": headers, "rows": detail,
                       "money_cols": (12, 14, 15), "total_row": d_total})
    return _resp(build_xlsx(sheets), f"{fname}.xlsx", "xlsx")
