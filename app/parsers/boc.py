# -*- coding: utf-8 -*-
"""中国银行流水解析。

文件特征：多 sheet，主表「详单」42 列中英双语列名、表头就在第 1 行；
金额带符号（来账为正、往账为负），余额带千分位逗号。
付款人/收款人各一套列，需按「交易类型」判断哪一侧是对方。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Tuple

from .base import TxnRecord, to_date, to_time, to_decimal, clean_str, is_self, ParseError
from .common import read_rows, build_header_map, cell, require_fields
from . import config as C


def parse(path: str) -> Tuple[List[TxnRecord], List[Dict[str, Any]]]:
    cfg = C.BOC
    rows = read_rows(path, sheet=cfg["sheet"])
    if not rows:
        raise ParseError("中行文件为空")

    idx = build_header_map(rows[cfg["header_row"]], cfg["columns"])
    require_fields(idx, ["amount", "txn_date"], "中行")

    out: List[TxnRecord] = []
    skipped: List[Dict[str, Any]] = []

    for r in range(cfg["data_start"], len(rows)):
        row = rows[r]
        if not row or all(v is None or str(v).strip() == "" for v in row):
            continue

        amt = to_decimal(cell(row, idx, "amount"))
        d = to_date(cell(row, idx, "txn_date"))
        if amt is None or d is None:
            if any(str(v).strip() for v in row if v is not None):
                skipped.append({"row": r + 1, "reason": "金额或交易日期无法解析",
                                "raw": [str(v)[:30] for v in row[:6]]})
            continue
        if amt == 0:
            skipped.append({"row": r + 1, "reason": "金额为 0", "raw": [
                str(cell(row, idx, "memo"))[:40]]})
            continue

        txn_type = clean_str(cell(row, idx, "txn_type")) or ""
        payer = clean_str(cell(row, idx, "payer_name"))
        payee = clean_str(cell(row, idx, "payee_name"))
        payer_acc = clean_str(cell(row, idx, "payer_account"))
        payee_acc = clean_str(cell(row, idx, "payee_account"))
        payer_bank = clean_str(cell(row, idx, "payer_bank"))
        payee_bank = clean_str(cell(row, idx, "payee_bank"))

        # ---- 判定收/支：优先看交易类型，缺失时退回金额符号 ----
        if txn_type == cfg["incoming_type"]:
            direction = "收"
        elif txn_type == cfg["outgoing_type"]:
            direction = "支"
        else:
            direction = "收" if amt > 0 else "支"

        # ---- 对方：收→付款人，支→收款人；若判出来的"对方"是本方则说明取反了 ----
        if direction == "收":
            cp_name, cp_acc, cp_bank = payer, payer_acc, payer_bank
            self_acc = payee_acc
            if cp_name and is_self(cp_name, None) and payee and not is_self(payee, None):
                cp_name, cp_acc, cp_bank = payee, payee_acc, payee_bank
                self_acc = payer_acc
        else:
            cp_name, cp_acc, cp_bank = payee, payee_acc, payee_bank
            self_acc = payer_acc
            if cp_name and is_self(cp_name, None) and payer and not is_self(payer, None):
                cp_name, cp_acc, cp_bank = payer, payer_acc, payer_bank
                self_acc = payee_acc

        if not cp_name:
            cp_name = payer if direction == "支" else payee

        out.append(TxnRecord(
            bank=cfg["bank"],
            account_no=self_acc,
            account_name=None,
            txn_date=d,
            txn_time=to_time(cell(row, idx, "txn_time")),
            direction=direction,
            amount=abs(amt),
            balance=to_decimal(cell(row, idx, "balance")),
            counterparty_name_raw=cp_name,
            counterparty_account=cp_acc,
            counterparty_bank=cp_bank,
            memo=clean_str(cell(row, idx, "memo")),
            purpose=clean_str(cell(row, idx, "purpose")) or clean_str(cell(row, idx, "remark")),
            serial_no=clean_str(cell(row, idx, "serial_no")),
            row_no=r + 1,
        ))

    if not out:
        raise ParseError("中行文件未解析出任何有效的交易明细行")
    return out, skipped
