# -*- coding: utf-8 -*-
"""并发压测 + 接口回归：验证 SQLite 下不再出现 Exception in ASGI application。

用法：python scripts/stress_test.py [--base http://127.0.0.1:PORT] [--rounds N] [--workers N]
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

URLS = [
    "/api/reports/dashboard",
    "/api/reports/dashboard",
    "/api/reports/aging",
    "/api/reports/balance",
    "/api/reports/top",
    "/api/reports/trend",
    "/api/reports/expense?direction=%E8%BF%9B%E9%A1%B9",
    "/api/reports/expense?direction=%E9%94%80%E9%A1%B9",
    "/api/reconcile/workbench?scope=ar&size=20",
    "/api/reconcile/workbench?scope=ap&size=20",
    "/api/reconcile/workbench/detail?scope=ar&counterparty_id=704",
    "/api/reconcile/workbench/detail?scope=ap&counterparty_id=704",
    "/api/invoices?page=1&size=50",
    "/api/txns?page=1&size=50",
    "/api/reconcile/summary",
    "/api/counterparties?page=1&size=50",
]


def hit(base: str, u: str):
    try:
        with urllib.request.urlopen(base + u, timeout=60) as r:
            return r.status, r.read(300).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read(300).decode("utf-8", "ignore")
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--workers", type=int, default=24)
    a = ap.parse_args()

    tasks = URLS * a.rounds
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        res = list(ex.map(lambda u: hit(a.base, u), tasks))
    el = time.time() - t0

    cnt = collections.Counter(s for s, _ in res)
    print(f"总请求 {len(tasks)}  并发 {a.workers}  耗时 {el:.2f}s  QPS {len(tasks)/el:.1f}")
    print("状态码分布：", dict(cnt))

    bad = [(u, s, b) for u, (s, b) in zip(tasks, res) if s != 200]
    if bad:
        print(f"!!! 非 200 共 {len(bad)} 条")
        seen = set()
        for u, s, b in bad:
            if u in seen:
                continue
            seen.add(u)
            print(f"   [{s}] {u}\n        {b[:200]}")
        return 1

    # 压测后进程必须还活着（段错误会让进程直接消失）
    try:
        with urllib.request.urlopen(a.base + "/api/health", timeout=10) as r:
            h = json.loads(r.read().decode("utf-8"))["data"]
        print(f"[OK] 压测后进程存活，后端 = {h['database']}")
    except Exception as e:  # noqa: BLE001
        print(f"!!! 压测后进程已死：{type(e).__name__}: {e}")
        return 1

    # 抽查业务正确性
    with urllib.request.urlopen(a.base + "/api/reports/expense?direction=%E8%BF%9B%E9%A1%B9",
                                timeout=30) as r:
        d = json.loads(r.read().decode("utf-8"))["data"]["summary"]
    print("[OK] 全部 200，无 ASGI 异常")
    print(f"     费用分析：发票 {d['invoice_count']} / 明细 {d['item_count']} / "
          f"类别 {d['category_count']} / 未分类 {d['uncategorized_invoice_count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
