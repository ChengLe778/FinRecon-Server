# -*- coding: utf-8 -*-
"""建设银行流水解析。

文件特征：单 sheet，表头干净但 37 列里大量冗余（个性化信息 1~10）。
借贷分列：借方发生额（支取）= 支出，贷方发生额（收入）= 收入。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Tuple

from .base import TxnRecord, to_date, to_time, to_decimal, clean_str, ParseError
from .common import read_rows, build_header_map, cell, require_fields
from . import config as C


def parse(path: str) -> Tuple[List[TxnRecord], List[Dict[str, Any]]]:
    cfg = C.CCB
    rows = read_rows(path)
    if len(rows) <= cfg["header_row"]:
        raise ParseError("建行文件行数不足，未找到表头行")

    idx = build_header_map(rows[cfg["header_row"]], cfg["columns"])
    require_fields(idx, ["txn_datetime", "debit", "credit"], "建行")

    out: List[TxnRecord] = []
    skipped: List[Dict[str, Any]] = []

    for r in range(cfg["data_start"], len(rows)):
        row = rows[r]
        if not row or all(v is None or str(v).strip() == "" for v in row):
            continue

        # 建行导出文件里每页都会重复一次表头，需要跳过
        if str(cell(row, idx, "account_no") or "").strip() == "账号":
            continue

        d = to_date(cell(row, idx, "txn_datetime"))
        if d is None:
            if any(str(v).strip() for v in row if v is not None):
                skipped.append({"row": r + 1, "reason": "无法解析交易时间",
                                "raw": [str(v)[:40] for v in row[:6]]})
            continue

        debit = to_decimal(cell(row, idx, "debit")) or Decimal("0")    # 支出
        credit = to_decimal(cell(row, idx, "credit")) or Decimal("0")  # 收入

        if debit == 0 and credit == 0:
            skipped.append({"row": r + 1, "reason": "借贷发生额均为 0", "raw": [
                str(cell(row, idx, "memo"))[:40]]})
            continue
        if debit > 0 and credit > 0:
            skipped.append({"row": r + 1, "reason": "借贷同时大于 0", "raw": [
                str(cell(row, idx, "memo"))[:40]]})
            continue

        direction = "收" if credit > 0 else "支"
        amount = credit if credit > 0 else debit

        out.append(TxnRecord(
            bank=cfg["bank"],
            account_no=clean_str(cell(row, idx, "account_no")),
            account_name=clean_str(cell(row, idx, "account_name")),
            txn_date=d,
            txn_time=to_time(cell(row, idx, "txn_datetime")),
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
        raise ParseError("建行文件未解析出任何有效的交易明细行")
    return out, skipped
