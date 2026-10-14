# -*- coding: utf-8 -*-
"""解析器注册与自动识别：先看文件名，再嗅探文件结构。"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple, Callable, Optional

from .base import ParseError
from .common import sheet_names, read_rows
from . import config as C
from . import abc_bank, boc, ccb, invoice

# 解析器类型常量
P_ABC = "abc"
P_BOC = "boc"
P_CCB = "ccb"
P_INVOICE = "invoice"

PARSERS: Dict[str, Dict[str, Any]] = {
    P_ABC: {"label": "农业银行流水", "fn": lambda: (abc_bank.parse,)},
    P_BOC: {"label": "中国银行流水", "fn": lambda: (boc.parse,)},
    P_CCB: {"label": "建设银行流水", "fn": lambda: (ccb.parse,)},
    P_INVOICE: {"label": "发票（进项/销项）", "fn": lambda: (invoice.parse,)},
}


def detect(path: str) -> str:
    """按文件名 → 文件结构 的顺序识别应使用的解析器。"""
    name = os.path.basename(path)

    # 1) 文件名命中
    if "发票" in name:
        return P_INVOICE
    if "农行" in name or "农业银行" in name:
        return P_ABC
    if "中行" in name or "中国银行" in name:
        return P_BOC
    if "建行" in name or "建设银行" in name:
        return P_CCB

    # 2) 结构嗅探
    try:
        names = sheet_names(path)
    except Exception:
        raise ParseError(f"无法打开文件：{name}")

    low = [str(n) for n in names]
    if any("发票" in n for n in low) or "发票基础信息" in low:
        return P_INVOICE
    if C.ABC["sheet"] in low:
        return P_ABC
    if C.BOC["sheet"] in low:
        return P_BOC

    # 3) 看表头里的特征列
    try:
        head = [str(x).strip() for x in (read_rows(path)[0] or []) if x is not None]
        h = " ".join(head)
        if "借方发生额" in h or "贷方发生额" in h:
            return P_CCB
        if "交易类型" in h and "交易金额" in h:
            return P_BOC
        if "收入金额" in h or "支出金额" in h:
            return P_ABC
        if "价税合计" in h or "销方识别号" in h:
            return P_INVOICE
    except Exception:
        pass

    raise ParseError(f"无法识别文件「{name}」的格式，请确认是银行流水或发票导出文件")


def assign_fingerprint_seq(records: List[Any]) -> List[Any]:
    """为同一指纹的重复记录编号（0,1,2...），按文件内出现顺序。

    银行流水没有唯一业务主键：同一秒可能发生多笔金额、摘要完全相同的手续费，
    它们是彼此独立的真实交易，不能丢弃。编号后首次导入可全部入库；
    重复上传同一份文件时编号一致，仍能被正确识别为重复。
    """
    seen: Dict[str, int] = {}
    for r in records:
        fp = r.fingerprint
        n = seen.get(fp, 0)
        r.fingerprint_seq = n
        seen[fp] = n + 1
    return records


def parse_file(path: str, parser_type: Optional[str] = None,
               **kwargs) -> Tuple[str, List[Any], List[Dict[str, Any]]]:
    """解析文件 → (解析器类型, 记录列表, 跳过的行)"""
    ptype = parser_type or detect(path)
    if ptype == P_ABC:
        recs, skipped = abc_bank.parse(path)
    elif ptype == P_BOC:
        recs, skipped = boc.parse(path)
    elif ptype == P_CCB:
        recs, skipped = ccb.parse(path)
    elif ptype == P_INVOICE:
        recs, skipped = invoice.parse(path, forced_direction=kwargs.get("direction"))
    else:
        raise ParseError(f"未知的解析器类型：{ptype}")
    recs = assign_fingerprint_seq(recs)
    return ptype, recs, skipped
