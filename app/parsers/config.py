# -*- coding: utf-8 -*-
"""解析配置：各银行的列名映射、表头位置、sheet 名。

银行改版时只需要改这里，不动解析逻辑。
"""
from ..config import BANK_ABC, BANK_BOC, BANK_CCB

# ------------------------------------------------------------------ 农业银行
ABC = {
    "bank": BANK_ABC,
    "sheet": "账户历史明细",
    "header_row": 2,          # 0-based：第 3 行是表头，前 3 行是账号/户名/币种元信息
    "data_start": 3,
    "meta_rows": [1],         # 从这一行提取 账号 / 户名
    "meta_patterns": {"account_no": r"账号[:：]\s*([^\s]+)", "account_name": r"户名[:：]\s*([^\s]+)"},
    "columns": {
        "txn_datetime": ["交易时间"],
        "income": ["收入金额"],
        "expense": ["支出金额"],
        "balance": ["账户余额"],
        "cp_account": ["对方账号"],
        "cp_name": ["对方户名"],
        "cp_bank": ["对方开户行"],
        "purpose": ["交易用途"],
        "memo": ["摘要"],
        "serial_no": ["账簿号"],
        "accounting_date": ["会计日期"],
    },
}

# ------------------------------------------------------------------ 中国银行
BOC = {
    "bank": BANK_BOC,
    "sheet": "详单",
    "header_row": 0,
    "data_start": 1,
    # 中行列名为中英双语，按「中文前缀」匹配
    "columns": {
        "txn_type": ["交易类型"],
        "payer_name": ["付款人名称"],
        "payer_account": ["付款人账号"],
        "payer_bank": ["付款人开户行名"],
        "payee_name": ["收款人名称"],
        "payee_account": ["收款人账号"],
        "payee_bank": ["收款人开户行名"],
        "txn_date": ["交易日期"],
        "txn_time": ["交易时间"],
        "amount": ["交易金额"],
        "balance": ["交易后余额"],
        "memo": ["摘要"],
        "purpose": ["用途"],
        "remark": ["交易附言"],
        "serial_no": ["交易流水号"],
    },
    "incoming_type": "来账",     # 来账 = 收
    "outgoing_type": "往账",     # 往账 = 支
}

# ------------------------------------------------------------------ 建设银行
CCB = {
    "bank": BANK_CCB,
    "header_row": 0,
    "data_start": 1,
    "columns": {
        "account_no": ["账号"],
        "account_name": ["账户名称"],
        "txn_datetime": ["交易时间"],
        "debit": ["借方发生额"],       # 支取
        "credit": ["贷方发生额"],      # 收入
        "balance": ["余额"],
        "currency": ["币种"],
        "cp_name": ["对方户名"],
        "cp_account": ["对方账号"],
        "cp_bank": ["对方开户机构"],
        "accounting_date": ["记账日期"],
        "memo": ["摘要"],
        "purpose": ["备注"],
        "serial_no": ["账户明细编号"],
    },
}

# ------------------------------------------------------------------ 发票
INVOICE = {
    "main_sheet": "发票基础信息",     # 一票一行，含 金额/税额/价税合计
    "detail_sheet": "信息汇总表",     # 一票多行明细（商品行）
    "header_row": 0,
    "columns": {
        "invoice_code": ["发票代码"],
        "invoice_no_old": ["发票号码"],
        "invoice_no": ["数电发票号码"],
        "seller_tax_id": ["销方识别号"],
        "seller_name": ["销方名称"],
        "buyer_tax_id": ["购方识别号"],
        "buyer_name": ["购买方名称"],
        "invoice_date": ["开票日期"],
        "amount": ["金额"],
        "tax": ["税额"],
        "total": ["价税合计"],
        "source": ["发票来源"],
        "invoice_type": ["发票票种"],
        "status": ["发票状态"],
        "is_positive": ["是否正数发票"],
        "remark": ["备注"],
    },
    "detail_columns": {
        "invoice_no": ["数电发票号码"],
        "seller_tax_id": ["销方识别号"],
        "seller_name": ["销方名称"],
        "tax_code": ["税收分类编码"],
        "item_name": ["货物或应税劳务名称"],
        "spec": ["规格型号"],
        "unit": ["单位"],
        "qty": ["数量"],
        "price": ["单价"],
        "amount": ["金额"],
        "tax_rate": ["税率"],
        "tax": ["税额"],
        "total": ["价税合计"],
    },
    # 发票状态中视为「作废/已红冲」、不计入余额的关键词
    "invalid_status_keywords": ["红冲", "作废"],
}
