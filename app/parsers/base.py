# -*- coding: utf-8 -*-
"""解析层公共模型与工具。

四类源文件最终全部归一为 TxnRecord / InvoiceRecord 两种结构，
对账与入库只认这两个模型。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from ..config import SELF_KEYWORDS, SELF_TAX_IDS, MONEY_QUANT


# ---------------------------------------------------------------- 数据模型

@dataclass
class TxnRecord:
    """银行流水统一模型"""
    bank: str
    account_no: Optional[str]
    account_name: Optional[str]
    txn_date: Optional[date]
    txn_time: Optional[str]
    direction: str                     # 收 / 支
    amount: Decimal                    # 恒为正数
    balance: Optional[Decimal]
    counterparty_name_raw: Optional[str]
    counterparty_account: Optional[str]
    counterparty_bank: Optional[str]
    memo: Optional[str]
    purpose: Optional[str]
    serial_no: Optional[str]
    row_no: int = 0
    # 同指纹内的序号：银行流水缺少唯一业务主键，同一秒内可能发生多笔金额、
    # 摘要完全相同的手续费，它们是彼此独立的真实交易。用序号区分，
    # 既保证首次导入不丢数据，重复上传整份文件时序号一致仍能被识别为重复。
    fingerprint_seq: int = 0
    raw: dict = field(default_factory=dict)

    def __post_init__(self):
        self.account_no = normalize_account(self.account_no)
        self.counterparty_account = normalize_account(self.counterparty_account)

    @property
    def fingerprint(self) -> str:
        key = "|".join([
            self.bank or "",
            self.account_no or "",
            self.txn_date.isoformat() if self.txn_date else "",
            self.txn_time or "",
            str(self.amount),
            self.direction or "",
            (self.memo or "").strip(),
            (self.purpose or "").strip(),
            (self.counterparty_name_raw or "").strip(),
            self.counterparty_account or "",
        ])
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        d["fingerprint"] = self.fingerprint
        return d


@dataclass
class ItemRecord:
    """发票明细行（一票多行）。费用分类的数据来源。"""
    name: Optional[str] = None             # 货物或应税劳务名称，形如「*餐饮服务*餐饮服务」
    tax_code: Optional[str] = None         # 税收分类编码
    spec: Optional[str] = None
    unit: Optional[str] = None
    qty: Optional[object] = None
    price: Optional[object] = None
    amount: object = None                  # 不含税金额
    tax_rate: Optional[str] = None
    tax: object = None
    total: object = None                   # 价税合计
    category: Optional[str] = None         # 费用类别
    row_no: int = 0


@dataclass
class InvoiceRecord:
    """发票统一模型"""
    direction: str                     # 销项 / 进项
    invoice_no: Optional[str]
    invoice_code: Optional[str]
    invoice_date: Optional[date]
    seller_name: Optional[str]
    seller_tax_id: Optional[str]
    buyer_name: Optional[str]
    buyer_tax_id: Optional[str]
    amount: Decimal                    # 不含税金额
    tax: Decimal                       # 税额
    total: Decimal                     # 价税合计
    invoice_type: Optional[str] = None
    status: Optional[str] = None       # 正常 / 已红冲-全额
    is_negative: bool = False          # 红字发票（金额为负）
    item_count: int = 1
    items: list = field(default_factory=list)   # List[ItemRecord] 明细行
    counterparty_name_raw: Optional[str] = None
    row_no: int = 0
    raw: dict = field(default_factory=dict)

    def __post_init__(self):
        self.seller_tax_id = normalize_account(self.seller_tax_id)
        self.buyer_tax_id = normalize_account(self.buyer_tax_id)

    @property
    def fingerprint(self) -> str:
        # 发票号可能为空（旧版增值税普通发票），故并入日期与金额降低碰撞概率
        key = "|".join([
            self.direction or "",
            (self.invoice_no or "").strip(),
            (self.seller_tax_id or "").strip(),
            self.invoice_date.isoformat() if self.invoice_date else "",
            str(self.total),
        ])
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        d["fingerprint"] = self.fingerprint
        return d


class ParseError(Exception):
    pass


# ---------------------------------------------------------------- 清洗工具

_ZERO = Decimal("0")


def to_decimal(value: Any) -> Optional[Decimal]:
    """任意单元格 → Decimal。禁止 float 中转，统一走字符串。"""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value.quantize(MONEY_QUANT)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value).quantize(MONEY_QUANT)
    if isinstance(value, float):
        # xlrd 返回的 float，先规整到 2 位避免二进制误差尾巴
        s = f"{value:.2f}"
    else:
        s = str(value).strip()
    if s == "" or s.lower() in ("nan", "none", "null", "-"):
        return None
    s = s.replace(",", "").replace("，", "").replace("¥", "").replace("￥", "")
    s = s.replace("(", "-").replace("（", "-").replace(")", "").replace("）", "")
    s = s.replace(" ", "")
    if s.endswith("%"):
        try:
            return (Decimal(s[:-1]) / Decimal(100)).quantize(MONEY_QUANT)
        except InvalidOperation:
            return None
    try:
        return Decimal(s).quantize(MONEY_QUANT)
    except InvalidOperation:
        return None


def _digits_only(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def to_date(value: Any) -> Optional[date]:
    """多种银行日期格式 → date。

    支持：2019-11-28 / 2019-11-28 11:55:50 / 20161201 / 20161201.0 /
          20160107 00:28:16 / 2025-12-26 13:53:45 / datetime / date
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    s = str(value).strip()
    if not s:
        return None

    # 纯 8 位数字（中行 20161201、建行记账日期 20160107），可能带 .0 尾巴
    m = re.fullmatch(r"(\d{8})(?:\.0+)?", s)
    if m:
        d = m.group(1)
        try:
            return date(int(d[:4]), int(d[4:6]), int(d[6:8]))
        except ValueError:
            return None

    # 8位数字 + 时间
    m = re.match(r"^(\d{8})[\sT]+(\d{2}):(\d{2})", s)
    if m:
        d = m.group(1)
        try:
            return date(int(d[:4]), int(d[4:6]), int(d[6:8]))
        except ValueError:
            return None

    # 标准日期 / 日期时间
    m = re.match(r"^(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    # Excel 序列号（数字）
    try:
        n = float(s)
        if 20000 < n < 80000:      # 约 1954 ~ 2119 年
            from datetime import timedelta
            return (datetime(1899, 12, 30) + timedelta(days=n)).date()
    except ValueError:
        pass
    return None


def to_time(value: Any) -> Optional[str]:
    """提取 HH:MM:SS"""
    if value is None:
        return None
    s = str(value).strip()
    m = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", s)
    if m:
        hh = m.group(1).zfill(2)
        mm = m.group(2)
        ss = m.group(3) or "00"
        return f"{hh}:{mm}:{ss}"
    m = re.fullmatch(r"(\d{2})(\d{2})(\d{2})", s)
    if m:
        return f"{m.group(1)}:{m.group(2)}:{m.group(3)}"
    return None


def clean_str(value: Any) -> Optional[str]:
    """单元格 → 干净字符串；空串转 None。"""
    if value is None:
        return None
    s = str(value).strip()
    if s.lower() in ("", "nan", "none", "null"):
        return None
    s = re.sub(r"\s+", " ", s)
    return s


def normalize_account(value: Optional[str]) -> Optional[str]:
    """账号标准化：去空格、去掉 xlrd 读出的 .0 尾巴、去掉全角空格。

    同一账号在不同行可能呈现为 6217003800001234567 / 6217003800001234567.0 / 6217 0038 0000 1234 567，
    不统一会导致去重指纹失效。
    """
    if value is None:
        return None
    s = str(value).strip().replace("\u3000", "")
    s = re.sub(r"\s+", "", s)
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    return s or None


def is_self(name: Optional[str], tax_id: Optional[str] = None) -> bool:
    """判断是否为本方主体（由 SELF_KEYWORDS / SELF_TAX_IDS 配置）。"""
    if tax_id:
        t = str(tax_id).strip().upper()
        if any(t == x.strip().upper() for x in SELF_TAX_IDS if x.strip()):
            return True
    if not name:
        return False
    n = str(name).strip()
    return any(kw in n for kw in SELF_KEYWORDS)


# ---------------------------------------------------------------- 名称归一

_NOISE = [
    "（", "）", "(", ")", " ", "\u3000", "·", "•", "-", "－", "_",
    "有限公司", "有限责任公司", "股份", "分公司", "公司", "集团",
]

_FULL2HALF = str.maketrans("（）　，。：；％", "() ,.:;%")


def normalize_name(name: Optional[str]) -> str:
    """名称归一化：全角转半角、去空白、去常见公司后缀、去全部标点。

    必须去掉标点，否则「XXX PTE. LTD.」与「XXX PTE LTD」会被当成两家单位。
    只保留中英文字符与数字。
    """
    if not name:
        return ""
    s = str(name).strip().translate(_FULL2HALF)
    s = re.sub(r"\s+", "", s)
    for n in _NOISE:
        s = s.replace(n, "")
    s = re.sub(r"[^\w\u4e00-\u9fff]", "", s)
    return s.upper()
