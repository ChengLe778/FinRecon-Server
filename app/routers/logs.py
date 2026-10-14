# -*- coding: utf-8 -*-
"""操作留痕查询"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import OperationLog
from ..response import ok

router = APIRouter(prefix="/api/logs", tags=["操作日志"])


@router.get("/operations")
def list_operations(page: int = 1, size: int = 30,
                    table_name: Optional[str] = None,
                    action: Optional[str] = None,
                    keyword: Optional[str] = None,
                    operator: Optional[str] = None,
                    db: Session = Depends(get_db)):
    q = db.query(OperationLog)
    if table_name:
        q = q.filter(OperationLog.table_name == table_name)
    if action:
        q = q.filter(OperationLog.action == action)
    if operator:
        q = q.filter(OperationLog.operator == operator)
    if keyword:
        q = q.filter(OperationLog.diff_json.like(f"%{keyword}%"))

    total = q.count()
    rows = q.order_by(OperationLog.created_at.desc()).offset(
        max(0, (page - 1) * size)).limit(size).all()

    items = []
    for r in rows:
        try:
            diff = json.loads(r.diff_json) if r.diff_json else None
        except Exception:
            diff = r.diff_json
        items.append({
            "id": r.id,
            "table_name": r.table_name,
            "record_id": r.record_id,
            "action": r.action,
            "operator": r.operator,
            "diff": diff,
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else None,
        })
    return ok({"items": items, "total": total, "page": page, "size": size})


@router.get("/meta")
def meta(db: Session = Depends(get_db)):
    """筛选项：表名与操作类型"""
    tables = [r[0] for r in db.query(OperationLog.table_name).distinct().all()]
    actions = [r[0] for r in db.query(OperationLog.action).distinct().all()]
    operators = [r[0] for r in db.query(OperationLog.operator).distinct().all()]
    return ok({"tables": tables, "actions": actions, "operators": operators})
