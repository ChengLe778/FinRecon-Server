# -*- coding: utf-8 -*-
"""数据导入：上传 → 解析预览（不入库）→ 确认 → 事务入库。

解析结果只存内存（token 索引），取消则不入库、不落临时表。
"""
from __future__ import annotations

import os
import uuid
import traceback
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List

from fastapi import APIRouter, UploadFile, File, Form, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..database import get_db
from ..config import UPLOAD_DIR
from ..deps import get_operator, write_log
from ..models import Invoice, InvoiceItem, BankTxn, ImportLog
from ..category import classify_invoice, CATEGORIES
from ..parsers import parse_file, detect, PARSERS, ParseError
from ..parsers.base import TxnRecord, InvoiceRecord
from ..services import build_alias_index, resolve_counterparty
from ..response import ok, fail, BizError

router = APIRouter(prefix="/api/import", tags=["导入"])

# 解析结果缓存（纯内存，进程重启即失效；全量约 1 万行，内存无压力）
PARSE_CACHE: Dict[str, dict] = {}
MAX_CACHE = 20


def _evict_cache():
    while len(PARSE_CACHE) > MAX_CACHE:
        oldest = next(iter(PARSE_CACHE))
        PARSE_CACHE.pop(oldest, None)


def _record_to_row(r) -> Dict[str, Any]:
    if isinstance(r, TxnRecord):
        return {
            "类型": "流水",
            "日期": str(r.txn_date or ""),
            "银行": r.bank,
            "方向": r.direction,
            "金额": str(r.amount),
            "对方": r.counterparty_name_raw or "",
            "摘要": r.memo or "",
            "用途": r.purpose or "",
            "账号": r.account_no or "",
        }
    return {
        "类型": "发票",
        "日期": str(r.invoice_date or ""),
        "方向": r.direction,
        "发票号": r.invoice_no or "",
        "价税合计": str(r.total),
        "金额": str(r.amount),
        "税额": str(r.tax),
        "对方": r.counterparty_name_raw or "",
        "状态": r.status or "",
        "票种": r.invoice_type or "",
    }


def _column_doc(ptype: str) -> str:
    return {
        "abc": "农业银行「账户历史明细」：跳过前 3 行元信息，取 交易时间/收入金额/支出金额/对方户名/摘要/交易用途",
        "boc": "中国银行「详单」：42 列按中文前缀匹配；按交易类型（来账/往账）取对方；金额去千分位",
        "ccb": "建设银行：37 列中取 14 个有效列；借方发生额=支出、贷方发生额=收入；跳过页内重复表头",
        "invoice": "发票「发票基础信息」主表（一票一行）；「信息汇总表」仅统计明细行数；购销双方含本方主体即判定方向",
    }.get(ptype, "")


@router.post("/parse")
async def parse_upload(
    file: UploadFile = File(...),
    parser_type: str = Form(default=None),
    direction: str = Form(default=None),
    db: Session = Depends(get_db),
):
    """上传并解析，返回预览（不入库）。"""
    if not file.filename:
        raise BizError("未选择文件")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".xls", ".xlsx"):
        raise BizError(f"不支持的文件类型「{ext}」，请上传 .xls 或 .xlsx")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    save_path = UPLOAD_DIR / f"{ts}_{file.filename}"
    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    try:
        ptype, recs, skipped = parse_file(
            str(save_path),
            parser_type or None,
            direction=(direction or None),
        )
    except ParseError as e:
        raise BizError(str(e))
    except Exception as e:
        traceback.print_exc()
        raise BizError(f"解析失败：{e}")

    if not recs:
        raise BizError("未能从文件中解析出任何有效数据")

    # 与库中已有数据比对，统计重复
    if ptype == "invoice":
        existing = {fp for (fp,) in db.query(Invoice.fingerprint).all()}
        dup_in_file = sum(1 for r in recs if getattr(r, "fingerprint_seq", 0) > 0)
        dup_in_db = sum(1 for r in recs if r.fingerprint in existing)
        new_cnt = sum(1 for r in recs
                      if r.fingerprint not in existing
                      and getattr(r, "fingerprint_seq", 0) == 0)
    else:
        exist_q = db.query(BankTxn.fingerprint, BankTxn.fingerprint_seq).all()
        existing = {(fp, sq) for fp, sq in exist_q}
        dup_in_file = 0
        dup_in_db = sum(1 for r in recs
                        if (r.fingerprint, r.fingerprint_seq) in existing)
        new_cnt = len(recs) - dup_in_db

    token = uuid.uuid4().hex
    PARSE_CACHE[token] = {
        "path": str(save_path),
        "filename": file.filename,
        "parser_type": ptype,
        "records": recs,
        "skipped": skipped,
        "created_at": datetime.now(),
    }
    _evict_cache()

    preview_rows = [_record_to_row(r) for r in recs[:50]]

    return ok({
        "token": token,
        "filename": file.filename,
        "parser_type": ptype,
        "parser_label": PARSERS.get(ptype, {}).get("label", ptype),
        "column_doc": _column_doc(ptype),
        "total_rows": len(recs),
        "new_rows": new_cnt,
        "dup_in_db": dup_in_db,
        "dup_in_file": dup_in_file,
        "skipped_rows": len(skipped),
        "skipped_sample": skipped[:5],
        "preview": preview_rows,
    })


@router.post("/commit")
def _item_names_summary(items) -> str:
    """明细名称摘要：去重后拼成「名称 / 名称」，超长截断。"""
    seen, out = set(), []
    for it in items or []:
        n = (getattr(it, "name", None) or "").strip()
        if not n or n in seen:
            continue
        seen.add(n)
        out.append(n)
    s = " / ".join(out)
    return s[:1000]


def _build_invoice(r, alias_index: dict, db: Session) -> Invoice:
    """发票 ORM 对象 + 明细行 + 费用分类。"""
    items = list(getattr(r, "items", None) or [])
    main_cat, cats = classify_invoice(items)
    inv = Invoice(
        direction=r.direction,
        invoice_no=r.invoice_no,
        invoice_code=r.invoice_code,
        invoice_date=r.invoice_date,
        seller_name=r.seller_name,
        seller_tax_id=r.seller_tax_id,
        buyer_name=r.buyer_name,
        buyer_tax_id=r.buyer_tax_id,
        amount=r.amount, tax=r.tax, total=r.total,
        invoice_type=r.invoice_type,
        status=r.status,
        is_negative=r.is_negative,
        item_count=len(items) or 1,
        counterparty_name_raw=r.counterparty_name_raw,
        counterparty_id=resolve_counterparty(
            db, r.counterparty_name_raw, alias_index),
        source="import",
        fingerprint=r.fingerprint,
        category=main_cat,
        categories=",".join(cats) if cats else None,
        item_names=_item_names_summary(items),
    )
    inv.items = [
        InvoiceItem(
            name=it.name, tax_code=it.tax_code, spec=it.spec, unit=it.unit,
            qty=it.qty, price=it.price, amount=it.amount or 0,
            tax_rate=it.tax_rate, tax=it.tax or 0, total=it.total or 0,
            category=it.category, row_no=it.row_no,
        ) for it in items
    ]
    return inv


def commit_import(payload: dict,
                  db: Session = Depends(get_db),
                  operator: str = Depends(get_operator)):
    """确认入库：单事务批量写入，重复行自动跳过。"""
    token = (payload or {}).get("token")
    if not token or token not in PARSE_CACHE:
        raise BizError("预览数据已过期，请重新上传文件")

    item = PARSE_CACHE[token]
    recs = item["records"]
    ptype = item["parser_type"]
    alias_index = build_alias_index(db)

    imported = dup = skipped = 0
    try:
        if ptype == "invoice":
            existing = {fp for (fp,) in db.query(Invoice.fingerprint).all()}
            batch = []
            for r in recs:
                if r.fingerprint in existing:
                    dup += 1
                    continue
                if getattr(r, "fingerprint_seq", 0) > 0:
                    dup += 1          # 文件内完全重复的行
                    continue
                batch.append(_build_invoice(r, alias_index, db))
                existing.add(r.fingerprint)
                imported += 1
            db.add_all(batch)
            db.flush()               # 一次性写入发票与其明细行（级联）
        else:
            exist_q = db.query(BankTxn.fingerprint, BankTxn.fingerprint_seq).all()
            existing = {(fp, sq) for fp, sq in exist_q}
            for r in recs:
                key = (r.fingerprint, r.fingerprint_seq)
                if key in existing:
                    dup += 1
                    continue
                db.add(BankTxn(
                    bank=r.bank,
                    account_no=r.account_no,
                    account_name=r.account_name,
                    txn_date=r.txn_date,
                    txn_time=r.txn_time,
                    direction=r.direction,
                    amount=r.amount,
                    balance=r.balance,
                    counterparty_name_raw=r.counterparty_name_raw,
                    counterparty_account=r.counterparty_account,
                    counterparty_bank=r.counterparty_bank,
                    counterparty_id=resolve_counterparty(
                        db, r.counterparty_name_raw, alias_index),
                    memo=r.memo,
                    purpose=r.purpose,
                    serial_no=r.serial_no,
                    source="import",
                    fingerprint=r.fingerprint,
                    fingerprint_seq=r.fingerprint_seq,
                ))
                existing.add(key)
                imported += 1

        skipped = len(item["skipped"])
        db.add(ImportLog(
            filename=item["filename"], parser_type=ptype,
            total_rows=len(recs), dup_rows=dup,
            imported_rows=imported, skipped_rows=skipped,
            operator=operator,
            remark=f"列映射：{_column_doc(ptype)}",
        ))
        write_log(db, "import", None, "import", {
            "filename": item["filename"], "parser_type": ptype,
            "imported": imported, "duplicate": dup, "skipped": skipped,
        }, operator)
        db.commit()
    except Exception as e:
        db.rollback()
        traceback.print_exc()
        raise BizError(f"入库失败，已回滚：{e}")

    PARSE_CACHE.pop(token, None)
    return ok({
        "imported": imported, "duplicate": dup, "skipped": skipped,
        "parser_type": ptype,
    }, msg=f"导入完成：新增 {imported} 条，跳过重复 {dup} 条")


@router.post("/cancel")
def cancel_import(payload: dict):
    """取消导入：丢弃内存中的解析结果，不写入数据库。"""
    token = (payload or {}).get("token")
    if token and token in PARSE_CACHE:
        item = PARSE_CACHE.pop(token)
        try:
            os.remove(item["path"])
        except Exception:
            pass
    return ok(msg="已取消导入")


@router.get("/logs")
def import_logs(db: Session = Depends(get_db)):
    rows = db.query(ImportLog).order_by(ImportLog.imported_at.desc()).limit(50).all()
    return ok([{
        "id": r.id, "filename": r.filename,
        "parser_type": PARSERS.get(r.parser_type, {}).get("label", r.parser_type),
        "total_rows": r.total_rows, "imported_rows": r.imported_rows,
        "dup_rows": r.dup_rows, "skipped_rows": r.skipped_rows,
        "operator": r.operator,
        "imported_at": r.imported_at.strftime("%Y-%m-%d %H:%M:%S"),
    } for r in rows])


@router.get("/parsers")
def list_parsers():
    return ok([{"value": k, "label": v["label"]} for k, v in PARSERS.items()])
