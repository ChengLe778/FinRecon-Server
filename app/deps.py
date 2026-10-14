# -*- coding: utf-8 -*-
"""公共依赖：数据库会话、操作人、金额工具。"""
from decimal import Decimal
from datetime import date, datetime
from typing import Any, Optional

from fastapi import Depends, Header
from sqlalchemy.orm import Session
from sqlalchemy import func

from .database import get_db
from .config import DEFAULT_OPERATOR, MONEY_QUANT, SELF_KEYWORDS
from .models import OperationLog


def money(x: Any) -> Decimal:
    """任意值 → 两位小数 Decimal。"""
    if x is None:
        return Decimal("0.00")
    if isinstance(x, Decimal):
        return x.quantize(MONEY_QUANT)
    return Decimal(str(x)).quantize(MONEY_QUANT)


def d2s(x: Any) -> str:
    """Decimal → 字符串，避免 JSON 序列化成 float 丢失精度。"""
    return f"{money(x)}"


def get_operator(x_operator: Optional[str] = Header(default=None, alias="X-Operator")) -> str:
    """当前操作人。MVP 单人使用，多人版在此改为读取登录态。"""
    return (x_operator or DEFAULT_OPERATOR or "admin").strip()


def write_log(db: Session, table_name: str, record_id: Optional[int], action: str,
              diff: Any, operator: str):
    """写操作留痕。diff 为 dict 或 list，序列化为 JSON 文本。"""
    import json
    try:
        payload = json.dumps(diff, ensure_ascii=False, default=str)
    except Exception:
        payload = str(diff)
    db.add(OperationLog(table_name=table_name, record_id=record_id,
                        action=action, diff_json=payload, operator=operator))


def diff_fields(before: Any, after: dict, fields: list) -> dict:
    """对比前后值，产出 {字段: {"from": 旧, "to": 新}}。"""
    out = {}
    for f in fields:
        old = getattr(before, f, None)
        new = after.get(f, None)
        if old is None and new in (None, ""):
            continue
        if str(old if old is not None else "") != str(new if new is not None else ""):
            out[f] = {"from": str(old) if old is not None else None,
                      "to": str(new) if new is not None else None}
    return out


def active_recon_amount(db: Session, model, id_col, target_id: int) -> Decimal:
    """某张发票 / 某笔流水当前已核销金额 = SUM(active 核销记录.amount)。

    已核销金额不落库，由 reconciliation 表实时汇总，避免删除联动导致数据不一致。
    """
    from .models import Reconciliation
    v = db.query(func.coalesce(func.sum(Reconciliation.amount), 0)).filter(
        Reconciliation.status == "active",
        getattr(Reconciliation, id_col) == target_id,
    ).scalar()
    return money(v)
