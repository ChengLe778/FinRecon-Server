# -*- coding: utf-8 -*-
"""主流程验收测试：导入 → 归集 → 对账 → 报表 → 导出 → 增删改联动 → 日志"""
import sys, io, json, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import httpx

BASE = "http://127.0.0.1:8000"
c = httpx.Client(base_url=BASE, timeout=600.0)
FAILS = []


def data(r, label="", quiet=False):
    """成功返回 data，失败打印并返回 None"""
    try:
        d = r.json()
    except Exception:
        if not quiet:
            print(f"   [FAIL] {label}: 非JSON {r.text[:150]}")
        FAILS.append(label)
        return None
    if d.get("code") != 0:
        if not quiet:
            print(f"   [code={d.get('code')}] {label}: {d.get('msg')}")
        return None
    return d["data"]


# 待导入的样本文件（绝对路径，分号分隔）。本仓库不含任何真实数据文件，
# 请用环境变量传入自己的文件，例如：
#   set FINRECON_TEST_FILES=C:\data\abc.xlsx;C:\data\boc.xls
FILES = [p.strip() for p in os.environ.get("FINRECON_TEST_FILES", "").split(";") if p.strip()]

print("=" * 80)
print(f"1. 导入样本文件（共 {len(FILES)} 个）")
print("=" * 80)
if not FILES:
    print("   未设置 FINRECON_TEST_FILES，跳过导入步骤")
for f in FILES:
    name = f.split("/")[-1]
    with open(f, "rb") as fh:
        d = data(c.post("/api/import/parse",
                        files={"file": (name, fh, "application/octet-stream")}),
                 "parse " + name)
    if not d:
        continue
    d2 = data(c.post("/api/import/commit", json={"token": d["token"]}), "commit " + name)
    print(f"   {name:<38} 解析 {d['total_rows']:>5} 行 → 入库 {d2['imported']:>5}，"
          f"重复 {d2['duplicate']}，跳过 {d2['skipped']}")

print("\n   重复上传校验：")
with open(FILES[1], "rb") as fh:
    d = data(c.post("/api/import/parse",
                    files={"file": ("dup.xls", fh, "application/octet-stream")}), "re-parse")
if d:
    print(f"     再次解析中行文件：解析 {d['total_rows']} 行，"
          f"库中已存在 {d['dup_in_db']}，将新增 {d['new_rows']}（应为 0）")
    d2 = data(c.post("/api/import/commit", json={"token": d["token"]}), "re-commit")
    if d2:
        flag = "OK" if d2["imported"] == 0 else "FAIL"
        print(f"     [{flag}] 再次入库新增 {d2['imported']} 条，识别重复 {d2['duplicate']} 条")
        if d2["imported"] != 0:
            FAILS.append("重复上传未完全拦截")

print("\n" + "=" * 80)
print("2. 往来单位自动归集")
print("=" * 80)
d = data(c.post("/api/counterparties/auto-collect", json={"min_count": 2}), "auto-collect")
print("   ", d)
d = data(c.get("/api/counterparties"), "cp list")
if d:
    with_inv = [x for x in d if x["invoice_count"] > 0]
    print(f"   往来单位总数 {len(d)}，其中有发票往来的 {len(with_inv)} 家")
    top = sorted(with_inv, key=lambda x: -(float(x["ar_balance"]) + float(x["ap_balance"])))[:6]
    for x in top:
        print(f"     {x['name'][:24]:<26} {x['type']:<4} 应收 {x['ar_balance']:>13} "
              f"应付 {x['ap_balance']:>13}  票{x['invoice_count']:>4} 流水{x['txn_count']:>5}")

print("\n" + "=" * 80)
print("3. 对账引擎")
print("=" * 80)
for scope in ("ar", "ap"):
    d = data(c.post("/api/reconcile/run", json={"scope": scope, "auto_apply": True}),
             f"run-{scope}")
    if not d:
        continue
    r = d[scope]
    st = r["stats"]
    print(f"   [{r['label']}] 参与发票 {st['invoice_count']} 张 / 流水 {st['txn_count']} 笔")
    print(f"      精确匹配自动核销 {r['applied']} 条；"
          f"待人工确认候选 {len(r['candidates'])} 条")
    kinds = {}
    for p in r["candidates"]:
        kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
    print(f"      候选类型分布: {kinds}")

d = data(c.get("/api/reconcile/summary"), "summary")
if d:
    print("\n   核销后概览：")
    for k, v in d.items():
        print(f"     [{v['label']}] 开票 {v['invoice_total']:>14} "
              f"已核销 {v['invoice_reconciled']:>14} 未核销 {v['invoice_remaining']:>14}")

print("\n" + "=" * 80)
print("4. 报表")
print("=" * 80)
for scope in ("ar", "ap"):
    d = data(c.get("/api/reports/balance", params={"scope": scope}), f"balance-{scope}")
    if not d:
        continue
    print(f"   [{d['label']}余额表] 单位 {d['summary']['counterparty_count']} 家，"
          f"发票 {d['summary']['invoice_count']} 张，未核销 {d['summary']['remaining']}")
    for x in d["items"][:3]:
        print(f"     {x['counterparty'][:24]:<26} 未核销 {x['remaining']:>14}")

d = data(c.get("/api/reports/aging", params={"scope": "ar"}), "aging")
if d:
    print(f"\n   [应收账龄 as of {d['as_of']}]")
    for b in d["buckets"]:
        if b["count"]:
            print(f"     {b['bucket']:<10} {b['count']:>4} 张 {b['amount']:>15} ({b['ratio']:>5.1f}%)")
    print(f"     合计 {d['summary']['total']}")

d = data(c.get("/api/reports/top", params={"scope": "ar", "limit": 5}), "top")
if d:
    print("\n   [欠款 TOP5]")
    for x in d["items"]:
        print(f"     {x['counterparty'][:22]:<24} {x['remaining']:>14} "
              f"账龄 {x['oldest_days']:>4} 天 [{x['aging_bucket']:<8}] 风险 {x['risk']}")

d = data(c.get("/api/reports/trend", params={"months": 6}), "trend")
if d:
    print("\n   [近 6 月收付款]")
    for x in d["items"]:
        print(f"     {x['month']}  收 {x['income']:>14}  支 {x['expense']:>14}  净 {x['net']:>14}")

d = data(c.get("/api/reports/dashboard"), "dashboard")
if d:
    print("\n   [看板]")
    print(f"     应收余额 {d['ar']['balance']}  应付余额 {d['ap']['balance']}")
    print(f"     本月收 {d['cash']['month_income']}  本月付 {d['cash']['month_expense']}")
    print(f"     数据: 发票 {d['counts']['invoice']} 流水 {d['counts']['txn']} "
          f"往来单位 {d['counts']['counterparty']} 核销 {d['counts']['recon_active']} 条")

print("\n" + "=" * 80)
print("5. 导出")
print("=" * 80)
for name, url, params in [
    ("应收余额表", "/api/export/balance", {"scope": "ar", "fmt": "xlsx"}),
    ("应收余额表CSV", "/api/export/balance", {"scope": "ar", "fmt": "csv"}),
    ("应付账龄表", "/api/export/aging", {"scope": "ap", "fmt": "xlsx"}),
    ("发票明细CSV", "/api/export/invoices", {"fmt": "csv"}),
    ("流水明细CSV", "/api/export/txns", {"fmt": "csv"}),
    ("核销记录", "/api/export/reconcile", {"fmt": "xlsx"}),
]:
    r = c.get(url, params=params)
    size = len(r.content)
    okm = "OK " if r.status_code == 200 and size > 500 else "FAIL"
    if okm == "FAIL":
        FAILS.append("导出:" + name)
    print(f"   [{okm}] {name:<14} {size:>9} 字节")

print("\n" + "=" * 80)
print("6. 手工新增 / 核销 / 撤销 / 删除联动")
print("=" * 80)
d = data(c.post("/api/invoices", json={
    "direction": "销项", "invoice_no": "TEST20260001", "invoice_date": "2026-01-15",
    "seller_name": "示例科技有限公司", "buyer_name": "测试客户有限公司",
    "amount": "10000.00", "tax": "900.00", "total": "10900.00", "status": "正常"}), "new inv")
inv_id = d["id"] if d else None

d = data(c.post("/api/txns", json={
    "bank": "中国银行", "txn_date": "2026-01-20", "direction": "收",
    "amount": "10900.00", "counterparty_name": "测试客户有限公司", "memo": "测试收款"}), "new txn")
txn_id = d["id"] if d else None
print(f"   新增发票 #{inv_id}，新增流水 #{txn_id}")

if inv_id and txn_id:
    d = data(c.post("/api/reconcile/confirm", json={"pairs": [
        {"invoice_ids": [inv_id], "txn_ids": [txn_id], "amount": "10900.00"}]}), "recon")
    print("   手工核销:", d)
    d = data(c.get(f"/api/invoices/{inv_id}"), "inv")
    print(f"   发票：合计 {d['total']} 已核销 {d['reconciled']} 未核销 {d['remaining']}")

    rid = d["reconciliations"][0]["id"]
    data(c.post(f"/api/reconcile/{rid}/reverse", json={"reason": "测试撤销"}), "reverse")
    d = data(c.get(f"/api/invoices/{inv_id}"), "after reverse")
    print(f"   撤销后：已核销 {d['reconciled']} 未核销 {d['remaining']}"
          f"  {'OK' if d['reconciled'] == '0.00' else 'FAIL'}")
    if d["reconciled"] != "0.00":
        FAILS.append("撤销核销未回滚")

    data(c.post("/api/reconcile/confirm", json={"pairs": [
        {"invoice_ids": [inv_id], "txn_ids": [txn_id], "amount": "10900.00"}]}), "recon2")

    d = data(c.delete(f"/api/invoices/{inv_id}"), "del-no-force", quiet=True)
    print("   直接删除被拦截（需二次确认）:", d is None)

    d = data(c.delete(f"/api/invoices/{inv_id}", params={"force": "true"}), "del-force")
    if d:
        print("   强制删除:", d.get("msg") if isinstance(d, dict) and "msg" in d else d)

    d = data(c.get(f"/api/txns/{txn_id}"), "txn after")
    if d:
        okm = "OK" if d["remaining"] == "10900.00" else "FAIL"
        print(f"   [{okm}] 流水未核销金额回滚为 {d['remaining']}（应为 10900.00）")
        if d["remaining"] != "10900.00":
            FAILS.append("删除发票未回滚流水")

    data(c.delete(f"/api/txns/{txn_id}", params={"force": "true"}), "del-txn", quiet=True)
    print("   测试数据已清理")

print("\n" + "=" * 80)
print("7. 操作日志")
print("=" * 80)
d = data(c.get("/api/logs/operations", params={"size": 12}), "logs")
if d:
    print(f"   日志总数 {d['total']}，最近记录：")
    for x in d["items"]:
        s = json.dumps(x["diff"], ensure_ascii=False)
        print(f"     {x['created_at']} {x['table_name']:<15} {x['action']:<9} "
              f"#{str(x['record_id']):<6} {x['operator']:<6} {s[:52]}")

print("\n" + "=" * 80)
if FAILS:
    print(f"存在 {len(FAILS)} 项失败: {FAILS}")
else:
    print("全部关键校验通过")
print("=" * 80)
