# -*- coding: utf-8 -*-
"""生成合成演示数据。

⚠️ 本脚本产出的全部内容均为**程序随机生成的虚构数据**：
公司名称、纳税人识别号、银行账号、人员姓名、发票号码、金额、日期
全部由代码拼装，与任何真实企业、个人、账户无关，可用于公开仓库演示。

用法（在项目根目录执行）：

    python scripts/generate_demo_data.py             # 库非空时会拒绝执行
    python scripts/generate_demo_data.py --force     # 清空业务数据后重建
    python scripts/generate_demo_data.py --seed 42   # 换一份随机数据

生成规模：约 45 家往来单位 / 270 张发票 / 700 条明细 / 420 笔流水 / 若干核销记录。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import random
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

# 允许以脚本方式直接运行：把项目根目录塞进 sys.path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app import models  # noqa: E402
from app.category import classify  # noqa: E402

# ---------------------------------------------------------------- 本方主体
SELF_NAME = "示例科技有限公司"
SELF_TAX_ID = "91110108MA0EXAMPLE"

# 三个虚构账户（仅用于演示，非任何真实账号）
ACCOUNTS = [
    ("农业银行", "11-060104040012345"),
    ("中国银行", "3205000888012345"),
    ("建设银行", "11001000000053001234"),
]

REGION_CODES = ["110108", "310115", "440106", "510107", "330106",
                "320115", "420111", "610113", "120116", "500103"]
CITY = ["京华", "沪江", "岭南", "锦城", "钱塘", "金陵", "江城", "长安", "津海", "渝州"]
BRAND = ["云图", "星河", "启明", "宏远", "瑞泰", "嘉禾", "恒信", "智联",
         "朗新", "天成", "汇通", "金桥", "博远", "安泰"]
BIZ = ["信息技术", "电子科技", "智能设备", "建筑安装", "物业管理", "商贸",
       "供应链管理", "广告传媒", "人力资源", "物流", "机电设备", "网络科技"]
SUFFIX = ["有限公司", "有限责任公司", "股份有限公司", "集团有限公司"]

# （明细名称，税收分类编码，单位，税率%，不含税金额区间）
# 名称里 *xxx* 是税收分类大类，费用分类规则靠它稳定命中
ITEM_TEMPLATES = [
    ("*餐饮服务*餐饮服务", "3070401000000000000", "次", 6, 300, 4000),
    ("*住宿服务*住宿服务", "3070402000000000000", "间夜", 6, 300, 1600),
    ("*成品油*汽油92#", "1070101010000000000", "升", 13, 300, 900),
    ("*旅客运输服务*高铁票", "3010101000000000000", "次", 9, 100, 1200),
    ("*建筑服务*工程服务", "3050100000000000000", "项", 9, 3000, 60000),
    ("*计算机外部设备*打印机", "1090501010000000000", "台", 13, 800, 6000),
    ("*办公用品*复印纸", "1060201010000000000", "箱", 13, 100, 900),
    ("*咨询服务*咨询服务费", "3040502020000000000", "次", 6, 2000, 40000),
    ("*物流辅助服务*快递服务费", "3010502000000000000", "次", 6, 50, 900),
    ("*电信服务*通信服务费", "3030100000000000000", "月", 9, 100, 800),
    ("*物业管理服务*物业服务费", "3040601000000000000", "月", 6, 1000, 15000),
    ("*广告服务*广告发布费", "3040401000000000000", "次", 6, 1000, 30000),
    ("*会议服务*会议服务费", "3040501000000000000", "次", 6, 800, 12000),
    ("*劳动保护用品*劳保用品", "1060512000000000000", "件", 13, 100, 2000),
    ("*医疗服务*体检服务费", "3070301000000000000", "次", 6, 500, 8000),
    ("*金融服务*账户管理费", "3060100000000000000", "笔", 6, 5, 200),
    ("*公共安全设备*监控摄像机", "1090412000000000000", "台", 13, 300, 5000),
    ("*电线电缆*电力电缆", "1080107000000000000", "米", 13, 500, 30000),
    ("*照明装置*LED面板灯", "1090602000000000000", "套", 13, 100, 3000),
    ("*软件*管理软件授权", "1090203000000000000", "套", 13, 2000, 50000),
    ("*车辆维修服务*汽车维修费", "3070201000000000000", "次", 13, 300, 6000),
]
# 销项（我们开出去的）以服务与设备为主
SALE_ITEM_IDX = [4, 5, 7, 11, 12, 16, 18, 19]

MEMOS = ["货款", "劳务费", "往来款", "工程款", "设备款", "服务费",
         "报销款", "备用金", "月结款", "预付款"]
FEE_MEMOS = ["对公跨行转账汇款手续费", "账户维护费", "短信服务费",
             "批量结息", "电子银行年费", "回单服务费"]


def q2(v) -> Decimal:
    """两位小数量化（金额一律 Decimal，禁止浮点）。"""
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def sha256(*parts) -> str:
    return hashlib.sha256("|".join(str(p or "") for p in parts).encode("utf-8")).hexdigest()


def fake_tax_id(rng: random.Random) -> str:
    """虚构纳税人识别号：18 位，格式合法但纯属编造。"""
    alphabet = "0123456789ABCDEFGHJKLMNPQRTUWXY"
    body = "".join(rng.choice(alphabet) for _ in range(9))
    return rng.choice(REGION_CODES) + "MA" + body + rng.choice(alphabet)


def fake_invoice_no(rng: random.Random) -> str:
    """虚构数电发票号码：20 位纯数字。"""
    return "2" + str(rng.randint(10 ** 18, 10 ** 19 - 1))


def fake_card_no(rng: random.Random) -> str:
    """虚构对方账号：19 位纯数字。"""
    return str(rng.randint(10 ** 18, 10 ** 19 - 1))


def rand_amount(rng: random.Random, lo: int, hi: int) -> Decimal:
    """在 [lo, hi] 内取一个「像人工填的」金额：以整百/整千居多。"""
    v = rng.randint(lo, hi)
    r = rng.random()
    if r < 0.45:
        v = round(v / 100) * 100
    elif r < 0.70:
        v = round(v / 50) * 50
    if v <= 0:
        v = lo
    return q2(v)


# ---------------------------------------------------------------- 主体构建
def build_counterparties(rng: random.Random, n: int = 45):
    """生成虚构往来单位。"""
    rows, used = [], set()
    while len(rows) < n:
        name = f"{rng.choice(CITY)}{rng.choice(BRAND)}{rng.choice(BIZ)}{rng.choice(SUFFIX)}"
        if name in used:
            continue
        used.add(name)
        rows.append(name)
    # 再补几家个体工商户，贴近真实账套形态
    for i in range(5):
        rows.append(f"{rng.choice(CITY)}{rng.choice(BRAND)}餐饮店（个体工商户）")

    cps = []
    for i, name in enumerate(rows):
        if i < 12:
            ctype = "客户"           # 前 12 家是我们开票的对象（应收）
        elif "餐饮店" in name:
            ctype = "供应商"
        elif i < 34:
            ctype = "供应商"          # 中间是我们付款的对象（应付）
        else:
            ctype = "其他"
        cps.append(models.Counterparty(
            name=name,
            type=ctype,
            tax_id=fake_tax_id(rng),
            contact=None,
            phone=None,
            remark="演示数据",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        ))
    return cps


def build_invoice(rng: random.Random, cp: models.Counterparty, direction: str,
                  day: date, invoice_no: str):
    """生成一张发票及其明细行。"""
    if direction == "销项":                 # 我们开给客户
        seller_name, seller_tax = SELF_NAME, SELF_TAX_ID
        buyer_name, buyer_tax = cp.name, cp.tax_id
    else:                                   # 我们取得的进项票
        seller_name, seller_tax = cp.name, cp.tax_id
        buyer_name, buyer_tax = SELF_NAME, SELF_TAX_ID

    idx = rng.choice(SALE_ITEM_IDX) if direction == "销项" else rng.randrange(len(ITEM_TEMPLATES))
    n_items = 1 if rng.random() < 0.55 else rng.randint(2, 3)

    items, amount_sum = [], Decimal("0")
    tax_sum = Decimal("0")
    for k in range(n_items):
        t = ITEM_TEMPLATES[idx if k == 0 else rng.randrange(len(ITEM_TEMPLATES))]
        name, tax_code, unit, rate, lo, hi = t
        amt = rand_amount(rng, lo, hi)
        tax = q2(amt * Decimal(rate) / Decimal(100))
        cat = classify(name, tax_code) or "其他"
        items.append(models.InvoiceItem(
            row_no=k + 1, name=name, tax_code=tax_code, spec=None, unit=unit,
            qty=Decimal("1"), price=amt, amount=amt,
            tax_rate=f"{rate}%", tax=tax, total=amt + tax, category=cat,
        ))
        amount_sum += amt
        tax_sum += tax

    total = amount_sum + tax_sum
    fp = sha256(direction, invoice_no, seller_tax, day.isoformat(), total)
    inv = models.Invoice(
        direction=direction,
        invoice_no=invoice_no,
        invoice_code=None,
        invoice_date=day,
        seller_name=seller_name, seller_tax_id=seller_tax,
        buyer_name=buyer_name, buyer_tax_id=buyer_tax,
        amount=amount_sum, tax=tax_sum, total=total,
        invoice_type="电子普通发票" if rng.random() < 0.5 else "电子专用发票",
        status="正常",
        is_negative=False,
        item_count=len(items),
        counterparty_id=None,               # 提交后回填
        counterparty_name_raw=cp.name,
        source="demo",
        fingerprint=fp,
        remark=None,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    inv.items = items
    # 主类别 = 金额最大的明细行类别
    weights = {}
    for it in items:
        weights[it.category] = weights.get(it.category, Decimal("0")) + it.amount
    ordered = sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))
    inv.category = ordered[0][0]
    inv.categories = ",".join(c for c, _ in ordered)
    inv.item_names = "、".join(dict.fromkeys(it.name for it in items))[:1000]
    return inv


def _unique_no(rng: random.Random, used: set) -> str:
    """取一个未重复使用的虚构发票号码。"""
    for _ in range(50):
        no = fake_invoice_no(rng)
        if no not in used:
            used.add(no)
            return no
    raise RuntimeError("发票号池耗尽")


def build_txn(rng: random.Random, bank: str, account_no: str, day: date,
              direction: str, amount: Decimal, cp: models.Counterparty | None,
              memo: str | None, balance: Decimal, used_fp: set):
    """生成一条银行流水。"""
    for _ in range(12):                      # 极小概率撞指纹，重试即可
        t = f"{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}"
        fp = sha256(bank, account_no, day.isoformat(), t, amount, direction,
                    memo or "", "", cp.name if cp else "",
                    None if cp is None else cp._demo_account)
        if fp not in used_fp:
            used_fp.add(fp)
            break
    return models.BankTxn(
        bank=bank,
        account_no=account_no,
        account_name=SELF_NAME,
        txn_date=day,
        txn_time=t,
        direction=direction,
        amount=amount,
        balance=balance,
        counterparty_name_raw=cp.name if cp else None,
        counterparty_account=None if cp is None else cp._demo_account,
        counterparty_bank=None,
        counterparty_id=None,                # 提交后回填
        memo=memo,
        purpose=memo,
        serial_no=str(rng.randint(10 ** 15, 10 ** 16 - 1)),
        source="demo",
        fingerprint=fp,
        fingerprint_seq=0,
        remark=None,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )


# ---------------------------------------------------------------- 主流程
def generate(seed: int, force: bool) -> None:
    rng = random.Random(seed)

    init_db()
    db = SessionLocal()
    try:
        n_txn = db.query(models.BankTxn).count()
        n_inv = db.query(models.Invoice).count()
        if (n_txn or n_inv) and not force:
            print(f"数据库已有数据（发票 {n_inv} 张 / 流水 {n_txn} 笔）。")
            print("确认要清空重建请加 --force；或用 --seed 换一份随机数据。")
            return
        if force:
            print("清空既有业务数据 …")
            for t in (models.Reconciliation, models.InvoiceItem, models.Invoice,
                      models.BankTxn, models.CounterpartyAlias, models.Counterparty,
                      models.ImportLog, models.OperationLog):
                db.query(t).delete()
            db.commit()

        today = date.today()
        start = today - timedelta(days=365)

        # ---------------- 往来单位 ----------------
        cps = build_counterparties(rng)
        for cp in cps:
            cp._demo_account = fake_card_no(rng)
        db.add_all(cps)
        db.flush()
        print(f"往来单位 {len(cps)} 家")

        suppliers = [c for c in cps if c.type == "供应商"]
        customers = [c for c in cps if c.type == "客户"]
        others = [c for c in cps if c.type == "其他"]

        # ---------------- 发票 ----------------
        invoices, used_no = [], set()
        # 进项 200 张
        for _ in range(200):
            cp = rng.choice(suppliers + others)
            day = start + timedelta(days=rng.randrange(365))
            invoices.append(build_invoice(rng, cp, "进项", day, _unique_no(rng, used_no)))
        # 销项 70 张
        for _ in range(70):
            cp = rng.choice(customers)
            day = start + timedelta(days=rng.randrange(365))
            invoices.append(build_invoice(rng, cp, "销项", day, _unique_no(rng, used_no)))

        db.add_all(invoices)
        db.flush()
        for inv in invoices:
            inv.counterparty_id = inv.counterparty_name_raw and next(
                (c.id for c in cps if c.name == inv.counterparty_name_raw), None)
        db.flush()
        n_items = sum(len(i.items) for i in invoices)
        print(f"发票 {len(invoices)} 张 / 明细 {n_items} 行")

        # ---------------- 银行流水 ----------------
        used_fp = set()
        txns, pairs = [], []
        for inv in invoices:
            cp = next((c for c in cps if c.id == inv.counterparty_id), None)
            if cp is None:
                continue
            r = rng.random()
            if r > 0.82:
                continue                                   # 尚未收付款
            direction = "收" if inv.direction == "销项" else "支"
            bank, account_no = rng.choice(ACCOUNTS)
            day = inv.invoice_date + timedelta(days=rng.randint(-10, 45))
            day = min(max(day, start), today)
            memo = rng.choice(MEMOS)

            roll = rng.random()
            if roll < 0.62:
                amt = inv.total                            # 分文不差 → 自动核销
                exact = True
            elif roll < 0.78:
                amt = inv.total - q2(rng.randint(1, 30))   # 抹零 / 手续费
                exact = False
            elif roll < 0.90:
                amt = q2(inv.total * Decimal(rng.choice(["0.5", "0.6", "0.8"])))  # 部分收付
                exact = False
            else:
                amt = inv.total + q2(rng.randint(50, 900))  # 多收多付
                exact = False

            t = build_txn(rng, bank, account_no, day, direction, amt, cp, memo,
                          Decimal("0"), used_fp)
            txns.append(t)
            if exact:
                pairs.append((inv, t))

        # 银行手续费 / 结息：无对手方
        for _ in range(120):
            bank, account_no = rng.choice(ACCOUNTS)
            day = start + timedelta(days=rng.randrange(365))
            memo = rng.choice(FEE_MEMOS)
            if "结息" in memo:
                direction, amt = "收", rand_amount(rng, 10, 900)
            else:
                direction, amt = "支", rand_amount(rng, 5, 200)
            txns.append(build_txn(rng, bank, account_no, day, direction, amt,
                                  None, memo, Decimal("0"), used_fp))

        db.add_all(txns)
        db.flush()
        # 回填对手方 + 计算余额（按账户按日期滚动）
        cp_by_name = {c.name: c.id for c in cps}
        for t in txns:
            if t.counterparty_name_raw:
                t.counterparty_id = cp_by_name.get(t.counterparty_name_raw)
        for bank, account_no in ACCOUNTS:
            acc_txns = sorted([t for t in txns if t.bank == bank],
                              key=lambda x: (x.txn_date, x.txn_time or ""))
            bal = Decimal("500000")
            for t in acc_txns:
                bal = bal + t.amount if t.direction == "收" else bal - t.amount
                t.balance = bal
        db.flush()
        print(f"银行流水 {len(txns)} 笔")

        # ---------------- 核销记录 ----------------
        recs = []
        for inv, t in pairs:
            if rng.random() < 0.85:
                recs.append(models.Reconciliation(
                    invoice_id=inv.id, txn_id=t.id, amount=t.amount,
                    diff_amount=Decimal("0"), diff_reason=None,
                    method="auto", status="active", created_by="demo",
                    created_at=datetime.now(), remark="演示：金额一致自动核销",
                ))
        # 少量人工核销（带差额）
        unmatched = [t for t in txns if t.counterparty_id and
                     t.id not in {r.txn_id for r in recs}]
        for t in unmatched[:12]:
            inv = next((i for i in invoices
                        if i.counterparty_id == t.counterparty_id
                        and i.total > t.amount
                        and i.id not in {r.invoice_id for r in recs}), None)
            if inv is None:
                continue
            recs.append(models.Reconciliation(
                invoice_id=inv.id, txn_id=t.id, amount=t.amount,
                diff_amount=inv.total - t.amount,
                diff_reason=rng.choice(["抹零", "手续费", "汇损"]),
                method="manual", status="active", created_by="demo",
                created_at=datetime.now(), remark="演示：人工确认差额",
            ))
        db.add_all(recs)

        # ---------------- 别名 / 导入记录 / 操作日志 ----------------
        for cp in cps[:3]:
            db.add(models.CounterpartyAlias(
                counterparty_id=cp.id,
                alias=cp.name.replace("有限公司", "").strip(),
                created_at=datetime.now(),
            ))

        db.add_all([
            models.ImportLog(filename="demo-bank-abc.xlsx", parser_type="abc",
                             total_rows=len(txns), dup_rows=0,
                             imported_rows=len(txns), skipped_rows=0,
                             operator="demo", remark="演示数据：农业银行流水",
                             imported_at=datetime.now()),
            models.ImportLog(filename="demo-invoice-in.xlsx", parser_type="invoice",
                             total_rows=200, dup_rows=0, imported_rows=200,
                             skipped_rows=0, operator="demo",
                             remark="演示数据：进项发票", imported_at=datetime.now()),
            models.ImportLog(filename="demo-invoice-out.xlsx", parser_type="invoice",
                             total_rows=70, dup_rows=0, imported_rows=70,
                             skipped_rows=0, operator="demo",
                             remark="演示数据：销项发票", imported_at=datetime.now()),
        ])
        db.add_all([
            models.OperationLog(table_name="invoice", record_id=invoices[0].id,
                                action="create", diff_json='{"source": "demo"}',
                                operator="demo", created_at=datetime.now()),
            models.OperationLog(table_name="bank_txn", record_id=txns[0].id,
                                action="create", diff_json='{"source": "demo"}',
                                operator="demo", created_at=datetime.now()),
            models.OperationLog(table_name="reconciliation", record_id=None,
                                action="reconcile",
                                diff_json='{"auto": %d}' % len(recs),
                                operator="demo", created_at=datetime.now()),
        ])

        db.commit()
        print(f"核销记录 {len(recs)} 条")
        print("\n完成。启动服务后访问 http://localhost:8000 即可查看。")
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="生成合成演示数据")
    ap.add_argument("--force", action="store_true", help="清空业务数据后重建")
    ap.add_argument("--seed", type=int, default=20260101, help="随机数种子")
    a = ap.parse_args()
    generate(a.seed, a.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
