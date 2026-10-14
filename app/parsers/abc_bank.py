# -*- coding: utf-8 -*-
"""农业银行流水解析。

文件特征：单 sheet「账户历史明细」，第 1 行标题、第 2 行是账号/户名/币种元信息、
第 3 行才是表头。收付款金额分列（收入金额 / 支出金额）。
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Dict, List, Sequence, Tuple

from .base import TxnRecord, to_date, to_time, to_decimal, clean_str, ParseError
from .common import read_rows, build_header_map, cell, require_fields
from . import config as C


def parse(path: str) -> Tuple[List[TxnRecord], List[Dict[str, Any]]]:
    cfg = C.ABC
    rows = read_rows(path, sheet=cfg["sheet"])
    if len(rows) <= cfg["header_row"]:
        raise ParseError("农行文件行数不足，未找到表头行")

    # --- 元信息：账号 / 户名 ---
    account_no = account_name = None
    for r in cfg["meta_rows"]:
        if r < len(rows):
            line = " ".join(str(x) for x in rows[r] if x is not None)
            for key, pat in cfg["meta_patterns"].items():
                m = re.search(pat, line)
                if m:
                    if key == "account_no":
                        account_no = m.group(1).strip()
                    else:
                        account_name = m.group(1).strip()

    idx = build_header_map(rows[cfg["header_row"]], cfg["columns"])
    require_fields(idx, ["txn_datetime", "income", "expense"], "农行")

    out: List[TxnRecord] = []
    skipped: List[Dict[str, Any]] = []

    for r in range(cfg["data_start"], len(rows)):
        row = rows[r]
        if not row or all(v is None or str(v).strip() == "" for v in row):
            continue

        dt_raw = cell(row, idx, "txn_datetime")
        d = to_date(dt_raw)
        if d is None:
            # 没有交易时间 → 非明细行（如页脚、合计行），跳过
            if any(str(v).strip() for v in row if v is not None):
                skipped.append({"row": r + 1, "reason": "无法解析交易时间",
                                "raw": [str(v)[:40] for v in row[:6]]})
            continue

        income = to_decimal(cell(row, idx, "income")) or Decimal("0")
        expense = to_decimal(cell(row, idx, "expense")) or Decimal("0")

        if income == 0 and expense == 0:
            skipped.append({"row": r + 1, "reason": "收入与支出金额均为 0", "raw": [
                str(cell(row, idx, "memo"))[:40]]})
            continue

        if income > 0 and expense > 0:
            skipped.append({"row": r + 1, "reason": "收入与支出同时大于 0", "raw": [
                str(cell(row, idx, "memo"))[:40]]})
            continue

        direction = "收" if income > 0 else "支"
        amount = income if income > 0 else expense

        out.append(TxnRecord(
            bank=cfg["bank"],
            account_no=account_no,
            account_name=account_name,
            txn_date=d,
            txn_time=to_time(dt_raw),
            direction=direction,
            amount=amount,
            balance=to_decimal(cell(row, idx, "balance")),
            counterparty_name_raw=clean_str(cell(row, idx, "cp_name")),
            counterparty_account=clean_str(cell(row, idx, "cp_account")),
            counterparty_bank=clean_str(cell(row, idx, "cp_bank")),
            memo=clean_str(cell(row, idx, "memo")),
            purpose=clean_str(cell(row, idx, "purpose")),
            serial_no=clean_str(cell(row, idx, "serial_no")),
            row_no=r + 1,
        ))

    if not out:
        raise ParseError("农行文件未解析出任何有效的交易明细行")
    return out, skipped
