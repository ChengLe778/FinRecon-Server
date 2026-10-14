# -*- coding: utf-8 -*-
"""对账引擎测试：跑应收/应付，看自动匹配与候选数量。"""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import httpx

BASE = "http://127.0.0.1:8000"
c = httpx.Client(base_url=BASE, timeout=300.0)


def data(r):
    d = r.json()
    if d.get("code") != 0:
        print("FAIL:", d.get("msg"))
        return None
    return d["data"]


print("=" * 78)
print("对账概览（跑引擎前）")
print("=" * 78)
d = data(c.get("/api/reconcile/summary"))
if d:
    for k, v in d.items():
        print(f"  [{v['label']}] 发票 {v['invoice_count']} 张，开票 {v['invoice_total']}，"
              f"未核销 {v['invoice_remaining']}")
        print(f"        流水 {v['txn_count']} 笔，金额 {v['txn_total']}，"
              f"未核销 {v['txn_remaining']}")

print("\n" + "=" * 78)
print("跑对账引擎（仅计算，不写库）")
print("=" * 78)
for scope in ("ar", "ap"):
    d = data(c.post("/api/reconcile/run", json={"scope": scope, "auto_apply": False}))
    if not d:
        continue
    r = d[scope]
    st = r["stats"]
    print(f"\n  [{r['label']}]")
    print(f"    参与发票 {st['invoice_count']} 张 / 参与流水 {st['txn_count']} 笔")
    print(f"    精确自动匹配: {st['auto_count']} 对，金额 {st['auto_amount']}")
    print(f"    候选推荐:     {st['candidate_count']} 条")
    print("    候选样例 5 条：")
    for p in r["candidates"][:5]:
        det = p["detail"]
        print(f"      [{p['kind']}] score={p['score']} "
              f"发票#{p['invoice_ids']} ← 流水#{p['txn_ids']} "
              f"金额 {p['amount']}")
        print(f"          {det.get('cp_name')} | {p['reason']}")
        if p["kind"] == "combo":
            print(f"          发票号: {det.get('invoice_nos')} 合计 {det.get('invoice_total')}")
        else:
            print(f"          发票 {det.get('invoice_no')} 未核销 {det.get('invoice_remaining')}"
                  f" / 流水 {det.get('txn_date')} 未核销 {det.get('txn_remaining')}"
                  f" [{det.get('bank')}]")
