# -*- coding: utf-8 -*-
"""往来单位台账：主数据增删改 + 别名对照 + 未认领名称归集"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from ..database import get_db
from ..deps import get_operator, write_log, money
from ..models import Counterparty, CounterpartyAlias, Invoice, BankTxn
from ..services import recon_sums, invoice_remaining, txn_remaining, \
    is_invoice_effective, apply_invoice_filters, apply_txn_filters
from ..parsers.base import normalize_name
from ..response import ok, BizError

router = APIRouter(prefix="/api/counterparties", tags=["往来单位"])


class CpIn(BaseModel):
    name: str
    type: str = "客户"
    tax_id: Optional[str] = None
    contact: Optional[str] = None
    phone: Optional[str] = None
    remark: Optional[str] = None
    aliases: Optional[List[str]] = None


class AliasIn(BaseModel):
    alias: str


def _serialize(cp: Counterparty, db: Session,
               inv_recon: Dict[int, Decimal],
               txn_recon: Dict[int, Decimal]) -> Dict[str, Any]:
    # 应收（销项发票未核销）
    ar = Decimal("0")
    for inv in db.query(Invoice).filter(Invoice.counterparty_id == cp.id,
                                        Invoice.direction == "销项").all():
        if is_invoice_effective(inv):
            ar += invoice_remaining(inv, inv_recon)
    # 应付（进项发票未核销）
    ap = Decimal("0")
    for inv in db.query(Invoice).filter(Invoice.counterparty_id == cp.id,
                                        Invoice.direction == "进项").all():
        if is_invoice_effective(inv):
            ap += invoice_remaining(inv, inv_recon)

    inv_cnt = db.query(func.count(Invoice.id)).filter(
        Invoice.counterparty_id == cp.id).scalar() or 0
    txn_cnt = db.query(func.count(BankTxn.id)).filter(
        BankTxn.counterparty_id == cp.id).scalar() or 0

    return {
        "id": cp.id, "name": cp.name, "type": cp.type,
        "tax_id": cp.tax_id, "contact": cp.contact,
        "phone": cp.phone, "remark": cp.remark,
        "ar_balance": str(ar), "ap_balance": str(ap),
        "invoice_count": inv_cnt, "txn_count": txn_cnt,
        "aliases": [{"id": a.id, "alias": a.alias} for a in cp.aliases],
        "created_at": cp.created_at.strftime("%Y-%m-%d %H:%M") if cp.created_at else None,
    }


@router.get("")
def list_counterparties(keyword: Optional[str] = None,
                        type: Optional[str] = None,
                        db: Session = Depends(get_db)):
    q = db.query(Counterparty)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(or_(Counterparty.name.like(like),
                         Counterparty.tax_id.like(like)))
    if type:
        q = q.filter(Counterparty.type == type)
    rows = q.order_by(Counterparty.name).all()
    inv_recon, txn_recon = recon_sums(db)
    return ok([_serialize(r, db, inv_recon, txn_recon) for r in rows])


@router.get("/unclaimed")
def unclaimed(db: Session = Depends(get_db)):
    """尚未归属往来单位的原始名称（流水 + 发票），供台账页一键认领。"""
    buckets: Dict[str, Dict[str, Any]] = {}

    for name, cnt, amt in (db.query(BankTxn.counterparty_name_raw,
                                    func.count(BankTxn.id), func.sum(BankTxn.amount))
                           .filter(BankTxn.counterparty_id.is_(None),
                                   BankTxn.counterparty_name_raw.isnot(None),
                                   BankTxn.counterparty_name_raw != "")
                           .group_by(BankTxn.counterparty_name_raw).all()):
        k = normalize_name(name)
        if not k:
            continue
        b = buckets.setdefault(k, {"name": name, "variants": set(),
                                   "txn_count": 0, "txn_amount": Decimal("0"),
                                   "invoice_count": 0, "invoice_amount": Decimal("0")})
        b["variants"].add(name)
        b["txn_count"] += cnt or 0
        b["txn_amount"] += money(amt)

    for name, cnt, amt in (db.query(Invoice.counterparty_name_raw,
                                    func.count(Invoice.id), func.sum(Invoice.total))
                           .filter(Invoice.counterparty_id.is_(None),
                                   Invoice.counterparty_name_raw.isnot(None),
                                   Invoice.counterparty_name_raw != "")
                           .group_by(Invoice.counterparty_name_raw).all()):
        k = normalize_name(name)
        if not k:
            continue
        b = buckets.setdefault(k, {"name": name, "variants": set(),
                                   "txn_count": 0, "txn_amount": Decimal("0"),
                                   "invoice_count": 0, "invoice_amount": Decimal("0")})
        b["variants"].add(name)
        b["invoice_count"] += cnt or 0
        b["invoice_amount"] += money(amt)

    out = []
    for b in buckets.values():
        out.append({
            "name": sorted(b["variants"], key=len)[-1],   # 取最长的写法作为显示名
            "variants": sorted(b["variants"]),
            "txn_count": b["txn_count"],
            "txn_amount": str(b["txn_amount"]),
            "invoice_count": b["invoice_count"],
            "invoice_amount": str(b["invoice_amount"]),
        })
    out.sort(key=lambda x: -(x["txn_count"] + x["invoice_count"]))
    return ok(out)


@router.post("/claim")
def claim(payload: dict, db: Session = Depends(get_db),
          operator: str = Depends(get_operator)):
    """把一批原始名称归集为一个往来单位（自动写入别名）。"""
    names = (payload or {}).get("names") or []
    cp_type = (payload or {}).get("type") or "客户"
    cp_id = (payload or {}).get("counterparty_id")

    if not names and not cp_id:
        raise BizError("缺少要归集的名称")

    if cp_id:
        cp = db.get(Counterparty, cp_id)
        if not cp:
            raise BizError("目标往来单位不存在")
    else:
        main_name = max(names, key=len)
        exist = db.query(Counterparty).filter(Counterparty.name == main_name).first()
        if exist:
            cp = exist
        else:
            cp = Counterparty(name=main_name, type=cp_type)
            db.add(cp)
            db.flush()
            write_log(db, "counterparty", cp.id, "create",
                      {"name": cp.name, "type": cp_type, "from": "认领归集"},
                      operator)

    # 写入别名
    have = {a.alias for a in cp.aliases}
    for n in names:
        if n and n not in have and n != cp.name:
            db.add(CounterpartyAlias(counterparty_id=cp.id, alias=n))
            have.add(n)

    # 归属：按原始名称精确匹配 + 归一化匹配
    idx = {normalize_name(n) for n in names}
    affected_txn = affected_inv = 0

    for t in db.query(BankTxn).filter(BankTxn.counterparty_id.is_(None),
                                      BankTxn.counterparty_name_raw.isnot(None)).all():
        if normalize_name(t.counterparty_name_raw) in idx:
            t.counterparty_id = cp.id
            affected_txn += 1
    for i in db.query(Invoice).filter(Invoice.counterparty_id.is_(None),
                                      Invoice.counterparty_name_raw.isnot(None)).all():
        if normalize_name(i.counterparty_name_raw) in idx:
            i.counterparty_id = cp.id
            affected_inv += 1

    write_log(db, "counterparty", cp.id, "update", {
        "action": "认领归集", "names": names,
        "affected_txn": affected_txn, "affected_invoice": affected_inv,
    }, operator)
    db.commit()
    return ok({"counterparty_id": cp.id, "affected_txn": affected_txn,
               "affected_invoice": affected_inv},
              msg=f"已归集到「{cp.name}」：流水 {affected_txn} 笔、发票 {affected_inv} 张")


@router.post("/auto-collect")
def auto_collect(payload: dict = None, db: Session = Depends(get_db),
                 operator: str = Depends(get_operator)):
    """一键归集：把所有未认领的原始名称按归一化结果自动建为往来单位。

    类型判定：出现在销项发票里 → 客户；只出现在进项发票里 → 供应商；
    仅有流水 → 按流水收支方向判断（我们付钱给他 → 供应商）。
    """
    from ..parsers.base import normalize_name

    min_count = int((payload or {}).get("min_count", 1) or 1)

    buckets: Dict[str, Dict[str, Any]] = {}
    for name, cnt in (db.query(BankTxn.counterparty_name_raw, func.count(BankTxn.id))
                      .filter(BankTxn.counterparty_id.is_(None),
                              BankTxn.counterparty_name_raw.isnot(None),
                              BankTxn.counterparty_name_raw != "")
                      .group_by(BankTxn.counterparty_name_raw).all()):
        k = normalize_name(name)
        if not k:
            continue
        b = buckets.setdefault(k, {"names": set(), "txn": 0, "inv": 0,
                                   "type": None, "tax_id": None})
        b["names"].add(name)
        b["txn"] += cnt or 0

    for name, cnt, direction, tax_id in db.query(
            Invoice.counterparty_name_raw, func.count(Invoice.id),
            Invoice.direction, Invoice.seller_tax_id).filter(
            Invoice.counterparty_id.is_(None),
            Invoice.counterparty_name_raw.isnot(None),
            Invoice.counterparty_name_raw != "").group_by(
            Invoice.counterparty_name_raw, Invoice.direction,
            Invoice.seller_tax_id).all():
        k = normalize_name(name)
        if not k:
            continue
        b = buckets.setdefault(k, {"names": set(), "txn": 0, "inv": 0,
                                   "type": None, "tax_id": None})
        b["names"].add(name)
        b["inv"] += cnt or 0
        if direction == "销项":
            b["type"] = "客户"          # 我们给他开票 → 他是客户
        elif b["type"] != "客户":
            b["type"] = "供应商"
        if tax_id and not b["tax_id"]:
            b["tax_id"] = tax_id

    # 预先把未认领的单据按归一化名称建索引，避免在循环里反复扫全表
    from collections import defaultdict
    txn_by_norm: Dict[str, list] = defaultdict(list)
    for t in db.query(BankTxn).filter(BankTxn.counterparty_id.is_(None),
                                      BankTxn.counterparty_name_raw.isnot(None)).all():
        k = normalize_name(t.counterparty_name_raw)
        if k:
            txn_by_norm[k].append(t)
    inv_by_norm: Dict[str, list] = defaultdict(list)
    for i in db.query(Invoice).filter(Invoice.counterparty_id.is_(None),
                                      Invoice.counterparty_name_raw.isnot(None)).all():
        k = normalize_name(i.counterparty_name_raw)
        if k:
            inv_by_norm[k].append(i)

    created = linked_txn = linked_inv = 0
    skipped = 0
    for k, b in buckets.items():
        if b["txn"] + b["inv"] < min_count:
            skipped += 1
            continue
        main = sorted(b["names"], key=len)[-1]

        # 若已存在同名/同别名的往来单位，直接复用
        idx = {normalize_name(cp.name): cp.id for cp in db.query(Counterparty).all()}
        for a, cid in db.query(CounterpartyAlias.alias,
                               CounterpartyAlias.counterparty_id).all():
            idx.setdefault(normalize_name(a), cid)
        cp_id = idx.get(k)

        if cp_id is None:
            cp = Counterparty(
                name=main,
                type=b["type"] or "其他",
                tax_id=b["tax_id"],
                remark="自动归集",
            )
            db.add(cp)
            db.flush()
            cp_id = cp.id
            created += 1
            have = set()
            for n in b["names"]:
                if n != cp.name and n not in have:
                    db.add(CounterpartyAlias(counterparty_id=cp_id, alias=n))
                    have.add(n)

        for t in txn_by_norm.get(k, []):
            if t.counterparty_id is None:
                t.counterparty_id = cp_id
                linked_txn += 1
        for i in inv_by_norm.get(k, []):
            if i.counterparty_id is None:
                i.counterparty_id = cp_id
                linked_inv += 1

    write_log(db, "counterparty", None, "create", {
        "action": "一键自动归集", "created": created,
        "linked_txn": linked_txn, "linked_invoice": linked_inv,
    }, operator)
    db.commit()
    return ok({"created": created, "linked_txn": linked_txn,
               "linked_invoice": linked_inv, "skipped": skipped},
              msg=f"已新建 {created} 个往来单位，归集流水 {linked_txn} 笔、发票 {linked_inv} 张")


@router.post("")
def create_counterparty(body: CpIn, db: Session = Depends(get_db),
                        operator: str = Depends(get_operator)):
    if not body.name or not body.name.strip():
        raise BizError("名称不能为空")
    exist = db.query(Counterparty).filter(Counterparty.name == body.name.strip()).first()
    if exist:
        raise BizError(f"往来单位「{body.name}」已存在")

    cp = Counterparty(name=body.name.strip(), type=body.type or "客户",
                      tax_id=body.tax_id, contact=body.contact,
                      phone=body.phone, remark=body.remark)
    db.add(cp)
    db.flush()
    for a in (body.aliases or []):
        a = (a or "").strip()
        if a and a != cp.name:
            db.add(CounterpartyAlias(counterparty_id=cp.id, alias=a))
    write_log(db, "counterparty", cp.id, "create",
              {"name": cp.name, "type": cp.type, "aliases": body.aliases or []},
              operator)
    db.commit()
    return ok({"id": cp.id}, msg="往来单位已新增")


@router.put("/{cp_id}")
def update_counterparty(cp_id: int, body: CpIn, db: Session = Depends(get_db),
                        operator: str = Depends(get_operator)):
    cp = db.get(Counterparty, cp_id)
    if not cp:
        raise BizError("往来单位不存在")
    before = {"name": cp.name, "type": cp.type, "tax_id": cp.tax_id,
              "contact": cp.contact, "phone": cp.phone, "remark": cp.remark}

    cp.name = body.name.strip()
    cp.type = body.type or cp.type
    cp.tax_id = body.tax_id
    cp.contact = body.contact
    cp.phone = body.phone
    cp.remark = body.remark

    if body.aliases is not None:
        for a in list(cp.aliases):
            db.delete(a)
        db.flush()
        for a in body.aliases:
            a = (a or "").strip()
            if a and a != cp.name:
                db.add(CounterpartyAlias(counterparty_id=cp.id, alias=a))

    write_log(db, "counterparty", cp_id, "update",
              {"before": before,
               "after": {"name": cp.name, "type": cp.type, "tax_id": cp.tax_id,
                         "aliases": body.aliases}}, operator)
    db.commit()
    return ok({"id": cp.id}, msg="往来单位已更新")


@router.delete("/{cp_id}")
def delete_counterparty(cp_id: int, db: Session = Depends(get_db),
                        operator: str = Depends(get_operator)):
    cp = db.get(Counterparty, cp_id)
    if not cp:
        raise BizError("往来单位不存在")
    snapshot = {"name": cp.name, "type": cp.type, "tax_id": cp.tax_id,
                "aliases": [a.alias for a in cp.aliases]}
    # 关联单据的 counterparty_id 置空（ondelete=SET NULL），数据不丢
    db.query(BankTxn).filter(BankTxn.counterparty_id == cp_id).update(
        {BankTxn.counterparty_id: None})
    db.query(Invoice).filter(Invoice.counterparty_id == cp_id).update(
        {Invoice.counterparty_id: None})
    db.delete(cp)
    write_log(db, "counterparty", cp_id, "delete", snapshot, operator)
    db.commit()
    return ok(msg=f"往来单位「{snapshot['name']}」已删除（关联单据已解除归属，数据保留）")


@router.post("/{cp_id}/aliases")
def add_alias(cp_id: int, body: AliasIn, db: Session = Depends(get_db),
              operator: str = Depends(get_operator)):
    cp = db.get(Counterparty, cp_id)
    if not cp:
        raise BizError("往来单位不存在")
    alias = (body.alias or "").strip()
    if not alias:
        raise BizError("别名不能为空")
    if db.query(CounterpartyAlias).filter(CounterpartyAlias.counterparty_id == cp_id,
                                          CounterpartyAlias.alias == alias).first():
        raise BizError("该别名已存在")
    db.add(CounterpartyAlias(counterparty_id=cp_id, alias=alias))
    write_log(db, "counterparty", cp_id, "update",
              {"action": "新增别名", "alias": alias}, operator)
    db.commit()
    return ok(msg="别名已添加")


@router.delete("/{cp_id}/aliases/{alias_id}")
def delete_alias(cp_id: int, alias_id: int, db: Session = Depends(get_db),
                 operator: str = Depends(get_operator)):
    a = db.query(CounterpartyAlias).filter(CounterpartyAlias.id == alias_id,
                                           CounterpartyAlias.counterparty_id == cp_id).first()
    if not a:
        raise BizError("别名不存在")
    write_log(db, "counterparty", cp_id, "update",
              {"action": "删除别名", "alias": a.alias}, operator)
    db.delete(a)
    db.commit()
    return ok(msg="别名已删除")
