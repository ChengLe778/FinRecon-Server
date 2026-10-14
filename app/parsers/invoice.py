# -*- coding: utf-8 -*-
"""发票解析（取得发票=进项→应付 / 开具发票=销项→应收）。

文件特征：多 sheet。「发票基础信息」一票一行且带金额/税额/价税合计，作为主表；
「信息汇总表」一票多行商品明细，仅用于统计明细行数（item_count）。
其余专项 sheet（建筑服务/旅客运输服务等）都是主表的子集，不重复解析。

方向判定：销方是本方 → 销项（应收，对方=购方）；购方是本方 → 进项（应付，对方=销方）。
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from .base import InvoiceRecord, ItemRecord, to_date, to_decimal, clean_str, is_self, ParseError
from .common import read_rows, sheet_names, build_header_map, cell, require_fields
from . import config as C
from ..category import classify


def _num(value) -> Optional[object]:
    """数量 / 单价：保留原始精度，不做两位量化。"""
    from decimal import Decimal, InvalidOperation
    if value is None:
        return None
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def _parse_detail(path: str, names: list) -> Dict[Tuple[str, str], List[ItemRecord]]:
    """解析「信息汇总表」：按 (数电发票号码, 销方识别号) 归集明细行。"""
    cfg = C.INVOICE
    out: Dict[Tuple[str, str], List[ItemRecord]] = defaultdict(list)
    if cfg["detail_sheet"] not in names:
        return out
    try:
        drows = read_rows(path, sheet=cfg["detail_sheet"])
    except Exception:
        return out
    if len(drows) <= cfg["header_row"]:
        return out

    try:
        didx = build_header_map(drows[cfg["header_row"]], cfg["detail_columns"])
    except Exception:
        return out

    seq = 0
    for dr in drows[cfg["header_row"] + 1:]:
        if not dr or all(v is None or str(v).strip() == "" for v in dr):
            continue
        no = clean_str(cell(dr, didx, "invoice_no")) or ""
        sid = clean_str(cell(dr, didx, "seller_tax_id")) or ""
        name = clean_str(cell(dr, didx, "item_name"))
        if not no and not name:
            continue
        seq += 1
        amount = to_decimal(cell(dr, didx, "amount")) or Decimal("0")
        tax = to_decimal(cell(dr, didx, "tax")) or Decimal("0")
        total = to_decimal(cell(dr, didx, "total"))
        if total is None:
            total = amount + tax
        out[(no, sid)].append(ItemRecord(
            name=name,
            tax_code=clean_str(cell(dr, didx, "tax_code")),
            spec=clean_str(cell(dr, didx, "spec")),
            unit=clean_str(cell(dr, didx, "unit")),
            qty=_num(cell(dr, didx, "qty")),
            price=_num(cell(dr, didx, "price")),
            amount=amount,
            tax_rate=clean_str(cell(dr, didx, "tax_rate")),
            tax=tax,
            total=total,
            category=classify(name, clean_str(cell(dr, didx, "tax_code")),
                              clean_str(cell(dr, didx, "seller_name"))),
            row_no=seq,
        ))
    return out


def parse(path: str, forced_direction: Optional[str] = None) -> Tuple[List[InvoiceRecord], List[Dict[str, Any]]]:
    cfg = C.INVOICE
    names = sheet_names(path)

    main_sheet = cfg["main_sheet"] if cfg["main_sheet"] in names else names[0]
    rows = read_rows(path, sheet=main_sheet)
    if len(rows) <= cfg["header_row"]:
        raise ParseError("发票文件行数不足，未找到表头行")

    idx = build_header_map(rows[cfg["header_row"]], cfg["columns"])
    require_fields(idx, ["total"], "发票")

    # ---- 明细行：按 (数电发票号码, 销方识别号) 归集，用于费用分类 ----
    detail = _parse_detail(path, names)

    invalid_kws = cfg["invalid_status_keywords"]
    out: List[InvoiceRecord] = []
    skipped: List[Dict[str, Any]] = []

    for r in range(cfg["header_row"] + 1, len(rows)):
        row = rows[r]
        if not row or all(v is None or str(v).strip() == "" for v in row):
            continue

        total = to_decimal(cell(row, idx, "total"))
        if total is None:
            if any(str(v).strip() for v in row if v is not None):
                skipped.append({"row": r + 1, "reason": "价税合计无法解析",
                                "raw": [str(v)[:30] for v in row[:8]]})
            continue

        seller = clean_str(cell(row, idx, "seller_name"))
        buyer = clean_str(cell(row, idx, "buyer_name"))
        seller_tid = clean_str(cell(row, idx, "seller_tax_id"))
        buyer_tid = clean_str(cell(row, idx, "buyer_tax_id"))

        # ---- 方向判定 ----
        if forced_direction in ("销项", "进项"):
            direction = forced_direction
        elif is_self(seller, seller_tid):
            direction = "销项"          # 我们开的票 → 应收，对方是购方
        elif is_self(buyer, buyer_tid):
            direction = "进项"          # 我们取得的票 → 应付，对方是销方
        else:
            skipped.append({"row": r + 1, "reason": "购销双方均非本方主体，无法判定方向",
                            "raw": [str(seller)[:20], str(buyer)[:20]]})
            continue

        cp_name = buyer if direction == "销项" else seller

        amount = to_decimal(cell(row, idx, "amount")) or Decimal("0")
        tax = to_decimal(cell(row, idx, "tax")) or Decimal("0")
        d = to_date(cell(row, idx, "invoice_date"))
        invoice_no = clean_str(cell(row, idx, "invoice_no")) or clean_str(cell(row, idx, "invoice_no_old"))
        sid = seller_tid or ""
        status = clean_str(cell(row, idx, "status"))
        is_pos = clean_str(cell(row, idx, "is_positive"))

        # 明细行：精确匹配 (票号, 销方税号)；票号为空时退化为仅按销方税号兜底
        items = detail.get((invoice_no or "", sid), [])
        if not items and not invoice_no:
            items = detail.get(("", sid), [])

        out.append(InvoiceRecord(
            direction=direction,
            invoice_no=invoice_no,
            invoice_code=clean_str(cell(row, idx, "invoice_code")),
            invoice_date=d,
            seller_name=seller,
            seller_tax_id=seller_tid,
            buyer_name=buyer,
            buyer_tax_id=buyer_tid,
            amount=amount,
            tax=tax,
            total=total,
            invoice_type=clean_str(cell(row, idx, "invoice_type")),
            status=status,
            is_negative=(is_pos == "否") or total < 0,
            item_count=len(items) or 1,
            items=list(items),
            counterparty_name_raw=cp_name,
            row_no=r + 1,
        ))

    if not out:
        raise ParseError("发票文件未解析出任何有效行")
    return out, skipped
