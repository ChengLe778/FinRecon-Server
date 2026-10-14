# -*- coding: utf-8 -*-
"""ORM 模型：七张核心表（含往来单位别名表）。

金额一律 DECIMAL(16,2)，禁止浮点。
"""
from datetime import datetime, date
from decimal import Decimal

from sqlalchemy import (
    Column, Integer, String, Date, DateTime, Numeric, Text,
    ForeignKey, UniqueConstraint, Index, Boolean, text,
)
from sqlalchemy.orm import relationship

from .database import Base


def money():
    return Numeric(16, 2)


class Counterparty(Base):
    """往来单位（客户/供应商主数据）"""
    __tablename__ = "counterparty"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, index=True)
    type = Column(String(20), nullable=False, default="客户")   # 客户 / 供应商 / 其他
    tax_id = Column(String(50), nullable=True)
    contact = Column(String(100), nullable=True)
    phone = Column(String(50), nullable=True)
    remark = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now,
                        onupdate=datetime.now, nullable=False)

    aliases = relationship("CounterpartyAlias", back_populates="counterparty",
                           cascade="all, delete-orphan", passive_deletes=True)


class CounterpartyAlias(Base):
    """别名对照：解决简称 / 错别字 / 个人户名与单位名不一致"""
    __tablename__ = "counterparty_alias"
    __table_args__ = (
        UniqueConstraint("counterparty_id", "alias", name="uq_cp_alias"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    counterparty_id = Column(Integer,
                             ForeignKey("counterparty.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    alias = Column(String(200), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    counterparty = relationship("Counterparty", back_populates="aliases")


class Invoice(Base):
    """发票：销项（我们开的→应收）/ 进项（取得的→应付）"""
    __tablename__ = "invoice"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_invoice_fp"),
        Index("ix_invoice_dir_date", "direction", "invoice_date"),
        Index("ix_invoice_cp", "counterparty_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    direction = Column(String(10), nullable=False)        # 销项 / 进项
    invoice_no = Column(String(60), nullable=True, index=True)   # 数电发票号码
    invoice_code = Column(String(40), nullable=True)
    invoice_date = Column(Date, nullable=True, index=True)
    seller_name = Column(String(200), nullable=True)
    seller_tax_id = Column(String(50), nullable=True, index=True)
    buyer_name = Column(String(200), nullable=True)
    buyer_tax_id = Column(String(50), nullable=True, index=True)

    amount = Column(money(), nullable=False, default=0)   # 不含税金额
    tax = Column(money(), nullable=False, default=0)      # 税额
    total = Column(money(), nullable=False, default=0)    # 价税合计

    invoice_type = Column(String(60), nullable=True)      # 票种
    status = Column(String(30), nullable=True)            # 正常 / 已红冲-全额 ...
    is_negative = Column(Boolean, default=False, nullable=False)  # 红字（负数）发票
    item_count = Column(Integer, default=1, nullable=False)

    counterparty_id = Column(Integer,
                             ForeignKey("counterparty.id", ondelete="SET NULL"),
                             nullable=True)
    counterparty_name_raw = Column(String(200), nullable=True, index=True)

    source = Column(String(20), default="import", nullable=False)  # import / manual
    fingerprint = Column(String(64), nullable=False)
    remark = Column(String(500), nullable=True)

    # ---- 费用分类（由「信息汇总表」明细行自动归集）----
    # category   ：主类别，取该票中金额最大的明细行类别
    # categories ：该票涉及的全部类别，逗号分隔（一张票可能同时有餐饮+住宿）
    # item_names ：明细名称摘要，报表展示用
    category = Column(String(30), nullable=True, index=True)
    categories = Column(String(300), nullable=True)
    item_names = Column(String(1000), nullable=True)

    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now,
                        onupdate=datetime.now, nullable=False)

    counterparty = relationship("Counterparty")
    items = relationship("InvoiceItem", back_populates="invoice",
                         cascade="all, delete-orphan", passive_deletes=True)


class InvoiceItem(Base):
    """发票明细行（「信息汇总表」一票多行）：费用分类的数据来源。

    名称形如「*餐饮服务*餐饮服务」，星号内即税收分类大类，
    配合税收分类编码即可稳定归类到 餐饮 / 住宿 / 油费 / 交通 等。
    """
    __tablename__ = "invoice_item"
    __table_args__ = (
        Index("ix_item_invoice", "invoice_id"),
        Index("ix_item_category", "category"),
        Index("ix_item_name", "name"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    invoice_id = Column(Integer,
                        ForeignKey("invoice.id", ondelete="CASCADE"),
                        nullable=False)
    row_no = Column(Integer, default=0, nullable=False)

    name = Column(String(500), nullable=True)        # 货物或应税劳务名称
    tax_code = Column(String(60), nullable=True)     # 税收分类编码
    spec = Column(String(200), nullable=True)        # 规格型号
    unit = Column(String(30), nullable=True)
    qty = Column(Numeric(18, 4), nullable=True)
    price = Column(Numeric(20, 6), nullable=True)
    amount = Column(money(), nullable=False, default=0)   # 不含税金额
    tax_rate = Column(String(20), nullable=True)
    tax = Column(money(), nullable=False, default=0)
    total = Column(money(), nullable=False, default=0)    # 价税合计
    category = Column(String(30), nullable=True)          # 费用类别

    invoice = relationship("Invoice", back_populates="items")


class BankTxn(Base):
    """银行流水"""
    __tablename__ = "bank_txn"
    __table_args__ = (
        UniqueConstraint("fingerprint", "fingerprint_seq", name="uq_txn_fp"),
        Index("ix_txn_bank_date", "bank", "txn_date"),
        Index("ix_txn_cp", "counterparty_id"),
        Index("ix_txn_dir", "direction"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    bank = Column(String(30), nullable=False, index=True)
    account_no = Column(String(60), nullable=True)
    account_name = Column(String(200), nullable=True)
    txn_date = Column(Date, nullable=False, index=True)
    txn_time = Column(String(20), nullable=True)

    direction = Column(String(10), nullable=False)        # 收 / 支
    amount = Column(money(), nullable=False, default=0)   # 恒为正数
    balance = Column(money(), nullable=True)

    counterparty_name_raw = Column(String(200), nullable=True, index=True)
    counterparty_account = Column(String(60), nullable=True)
    counterparty_bank = Column(String(200), nullable=True)
    counterparty_id = Column(Integer,
                             ForeignKey("counterparty.id", ondelete="SET NULL"),
                             nullable=True)

    memo = Column(String(500), nullable=True)             # 摘要
    purpose = Column(String(500), nullable=True)          # 用途 / 备注
    serial_no = Column(String(80), nullable=True)         # 银行流水号

    source = Column(String(20), default="import", nullable=False)
    fingerprint = Column(String(64), nullable=False)
    # 同指纹内序号：银行流水无唯一业务主键，同一秒的多笔相同手续费靠它区分。
    # 唯一约束建在 (fingerprint, fingerprint_seq) 上，保证重复上传不会重复入库。
    fingerprint_seq = Column(Integer, default=0, nullable=False)
    remark = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now,
                        onupdate=datetime.now, nullable=False)

    counterparty = relationship("Counterparty")


class Reconciliation(Base):
    """核销记录：append-only 事件表。

    撤销 = 插入一条 status='reversed' 且 reverse_of 指向原记录的新行，
    原记录本身被标记为 reversed。历史永不丢失。
    """
    __tablename__ = "reconciliation"
    __table_args__ = (
        Index("ix_recon_invoice", "invoice_id"),
        Index("ix_recon_txn", "txn_id"),
        Index("ix_recon_status", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    invoice_id = Column(Integer,
                        ForeignKey("invoice.id", ondelete="CASCADE"),
                        nullable=False)
    txn_id = Column(Integer,
                    ForeignKey("bank_txn.id", ondelete="CASCADE"),
                    nullable=False)
    amount = Column(money(), nullable=False)
    # 差异金额：抹零 / 手续费 / 汇损等，核销时发票侧按 amount+diff_amount 结清，
    # 流水侧只认 amount（真实的钱）。差异在报表中单独统计，不污染现金流。
    diff_amount = Column(money(), nullable=False, default=0,
                         server_default=text("0"))
    diff_reason = Column(String(200), nullable=True)
    method = Column(String(20), default="auto", nullable=False)   # auto / manual
    status = Column(String(20), default="active", nullable=False)  # active / reversed
    created_by = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    reversed_at = Column(DateTime, nullable=True)
    reverse_of = Column(Integer,
                        ForeignKey("reconciliation.id", ondelete="SET NULL"),
                        nullable=True)
    remark = Column(String(500), nullable=True)

    invoice = relationship("Invoice")
    txn = relationship("BankTxn")


class ImportLog(Base):
    """导入记录"""
    __tablename__ = "import_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(300), nullable=False)
    parser_type = Column(String(30), nullable=False)
    total_rows = Column(Integer, default=0, nullable=False)
    dup_rows = Column(Integer, default=0, nullable=False)
    imported_rows = Column(Integer, default=0, nullable=False)
    skipped_rows = Column(Integer, default=0, nullable=False)
    operator = Column(String(50), nullable=True)
    imported_at = Column(DateTime, default=datetime.now, nullable=False)
    remark = Column(String(500), nullable=True)


class OperationLog(Base):
    """操作留痕：所有手工增删改"""
    __tablename__ = "operation_log"
    __table_args__ = (Index("ix_oplog_table", "table_name"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    table_name = Column(String(50), nullable=False)
    record_id = Column(Integer, nullable=True)
    action = Column(String(20), nullable=False)     # create / update / delete / reconcile / reverse / import
    diff_json = Column(Text, nullable=True)
    operator = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.now, index=True, nullable=False)
