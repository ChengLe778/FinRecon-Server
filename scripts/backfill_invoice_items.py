# -*- coding: utf-8 -*-
"""发票明细回填：给已入库但缺少明细行的发票补上「货物或应税劳务名称」与费用类别。

用途：老数据（升级前导入的发票）只有表头金额，没有明细，
      无法做餐饮 / 油费 / 住宿等费用分析。跑一次这个脚本即可补齐。

用法：
    python scripts/backfill_invoice_items.py                 # 自动扫描 数据/ 下的发票文件
    python scripts/backfill_invoice_items.py <你的发票文件.xlsx>

判定方式：用与导入时完全相同的指纹（方向+票号+销方税号+开票日期+价税合计）
定位库里的发票，已有明细的跳过，因此可重复执行、不会产生重复数据。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.database import SessionLocal, init_db, db_dialect  # noqa: E402
from app.models import Invoice, InvoiceItem  # noqa: E402
from app.parsers import parse_file  # noqa: E402
from app.category import classify_invoice  # noqa: E402
from app.deps import money  # noqa: E402


def _find_invoice_files() -> list:
    out = []
    for d in (ROOT / "数据", ROOT / "data" / "uploads"):
        if not d.exists():
            continue
        for f in sorted(d.glob("*.xlsx")):
            if "发票" in f.name:
                out.append(str(f))
    return out


def _summarize(items) -> str:
    seen, out = set(), []
    for it in items:
        n = (it.name or "").strip()
        if not n or n in seen:
            continue
        seen.add(n)
        out.append(n)
    return " / ".join(out)[:1000]


def backfill(paths: list, force: bool = False) -> dict:
    init_db()
    db = SessionLocal()
    stat = {"files": 0, "matched": 0, "updated": 0, "items": 0, "skipped": 0}
    try:
        for path in paths:
            try:
                ptype, recs, skipped = parse_file(path)
            except Exception as e:
                print(f"  [跳过] {Path(path).name}：{e}")
                continue
            if ptype != "invoice":
                print(f"  [跳过] {Path(path).name}：不是发票文件（{ptype}）")
                continue
            stat["files"] += 1
            print(f"  解析 {Path(path).name}：{len(recs)} 张票")

            n = 0
            for r in recs:
                if not r.items:
                    continue
                inv = db.query(Invoice).filter(
                    Invoice.fingerprint == r.fingerprint).first()
                if not inv:
                    continue
                stat["matched"] += 1
                if inv.items and not force:
                    stat["skipped"] += 1
                    continue

                if force:
                    for old in list(inv.items):
                        db.delete(old)
                    db.flush()

                for it in r.items:
                    db.add(InvoiceItem(
                        invoice_id=inv.id, name=it.name, tax_code=it.tax_code,
                        spec=it.spec, unit=it.unit, qty=it.qty, price=it.price,
                        amount=it.amount or 0, tax_rate=it.tax_rate,
                        tax=it.tax or 0, total=it.total or 0,
                        category=it.category, row_no=it.row_no,
                    ))
                    stat["items"] += 1

                main, cats = classify_invoice(r.items)
                inv.category = main
                inv.categories = ",".join(cats) if cats else None
                inv.item_names = _summarize(r.items)
                inv.item_count = len(r.items)
                stat["updated"] += 1

                n += 1
                if n % 200 == 0:
                    db.commit()
                    print(f"    已处理 {n} 张...")
            db.commit()
            print(f"    → 本文件更新 {n} 张")
    finally:
        db.close()
    return stat


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    paths = args or _find_invoice_files()
    if not paths:
        print("未找到发票文件，请手动指定路径")
        sys.exit(1)

    print("=" * 56)
    print(f"  发票明细回填（数据库：{db_dialect()}）")
    print("=" * 56)
    if force:
        print("  模式：--force（已存在明细的发票也会重新写入）")

    s = backfill(paths, force=force)
    print("-" * 56)
    print(f"  处理文件     {s['files']} 个")
    print(f"  匹配到发票   {s['matched']} 张")
    print(f"  更新发票     {s['updated']} 张")
    print(f"  写入明细行   {s['items']} 行")
    print(f"  跳过（已有） {s['skipped']} 张")
    print("=" * 56)
