# -*- coding: utf-8 -*-
"""费用类别重打：按最新的 app/category.py 规则，重算全部发票明细行与发票主类别。

什么时候用：
    调整了分类规则（新增类别 / 补关键词 / 补税收分类编码）之后，
    对已经入库的明细行重新归类，让报表立刻生效，无需重新导入发票。

用法：
    python scripts/recategorize.py            # 全量重算
    python scripts/recategorize.py --dry-run  # 只看会变成什么样，不落库
    python scripts/recategorize.py --only 其他  # 只重算当前类别为「其他」的行

说明：
    只改 invoice_item.category 与 invoice.category / invoice.categories，
    不动金额、不动核销记录，可重复执行。
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.database import SessionLocal, init_db, db_dialect  # noqa: E402
from app.models import Invoice, InvoiceItem  # noqa: E402
from app.category import classify, classify_invoice  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="按最新规则重打费用类别")
    ap.add_argument("--dry-run", action="store_true", help="只统计不落库")
    ap.add_argument("--only", default="", help="只重算当前类别为某个值的行，例如 --only 其他")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()
    try:
        q = db.query(InvoiceItem).join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        if args.only:
            q = q.filter(InvoiceItem.category == args.only)
        items = q.all()
        print("=" * 56)
        print(f"  费用类别重打（数据库：{db_dialect()}）"
              f"{'  [DRY-RUN]' if args.dry_run else ''}")
        print("=" * 56)
        print(f"  待处理明细行 {len(items)} 条")

        before: Counter = Counter()
        after: Counter = Counter()
        changed = 0
        touched_invoices = set()

        for it in items:
            inv = it.invoice
            old = it.category or "其他"
            new = classify(it.name, it.tax_code,
                           getattr(inv, "seller_name", None) or getattr(inv, "counterparty_name_raw", None))
            before[old] += 1
            after[new] += 1
            if new != old:
                changed += 1
                touched_invoices.add(it.invoice_id)
                if not args.dry_run:
                    it.category = new

        if not args.dry_run:
            # 重算受影响发票的主类别
            for inv_id in touched_invoices:
                inv = db.get(Invoice, inv_id)
                if not inv:
                    continue
                main_cat, cats = classify_invoice(list(inv.items))
                inv.category = main_cat
                inv.categories = ",".join(cats) if cats else None
            db.commit()

        print(f"  类别发生变化 {changed} 条"
              + ("" if args.dry_run else f"，影响发票 {len(touched_invoices)} 张"))
        print("-" * 56)
        print(f"  {'类别':<12}{'重打前':>8}{'重打后':>8}{'增减':>8}")
        keys = sorted(set(before) | set(after),
                      key=lambda k: -(after.get(k, 0)))
        for k in keys:
            b, a = before.get(k, 0), after.get(k, 0)
            if b == 0 and a == 0:
                continue
            print(f"  {k:<12}{b:>8}{a:>8}{a - b:>+8}")
        print("=" * 56)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
