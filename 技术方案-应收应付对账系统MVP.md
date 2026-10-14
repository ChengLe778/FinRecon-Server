# 应收应付对账系统 · 技术方案（MVP）

> 版本：V1.0
> 日期：2026-08-31
> 依据：《产品设计文档-应收应付对账系统MVP》V0.2 + 真实数据探测结论
> 前端形态决策：**Vue 3 CDN 无构建**（路线 B），不做 Node 工具链

---

## 1. 技术栈总览

| 层 | 选型 | 说明 |
|----|------|------|
| 前端 | Vue 3（CDN 全局构建）+ Element Plus（CDN）+ ECharts 5（CDN） | 无构建链，改完刷新即生效；延续现有 demo 形态 |
| 后端 | Python 3.12+ / FastAPI + Uvicorn | 单进程伺服 API 与前端静态文件 |
| ORM | SQLAlchemy 2.x + PyMySQL | 换数据库只改连接串 |
| 数据库 | MySQL 8.x（生产）；开发期可用 SQLite 过渡 | 通过 SQLAlchemy 抽象，DDL 用 DECIMAL(16,2) |
| Excel 解析 | pandas + openpyxl（.xlsx）+ xlrd（.xls） | xls 与 xlsx 双格式必须都支持 |
| 导出 | openpyxl 直接生成 | 多 sheet（汇总+明细）、CSV 用标准库 csv |
| 部署 | 本机/局域网单机，Windows 计划任务 mysqldump 每日备份 | 无容器、无 nginx，双击脚本即跑 |

**已确认排除**：Node/npm/Vite 工具链；服务端模板；第 6 个数据文件（20250219_….xlsx「账户明细查询」格式）不纳入解析模板。

## 2. 整体架构

```
浏览器
  │  http://localhost:8000
  ▼
FastAPI 单进程（Uvicorn）
  ├── /            → 返回前端静态文件（index.html + js/ + css/，CDN 加载 Vue/EP/ECharts）
  ├── /api/*       → JSON 接口（Pydantic 校验入参出参）
  ├── parsers/     → Excel 解析适配层（纯函数，pandas）
  ├── engine/      → 对账引擎（纯函数）
  └── export/      → xlsx/csv 生成（StreamingResponse 下发）
  ▼
SQLAlchemy → MySQL 8（六张核心表）
```

要点：

- **前后端同源**：FastAPI 用 StaticFiles 直接伺服前端目录，开发与部署都不存在跨域、不需要两个进程。
- **解析与对账引擎是纯函数模块**：输入 DataFrame / 查询结果，输出统一模型，不碰数据库。可单独跑脚本测试（M1 就这么验证）。
- **CDN 依赖**：Vue、Element Plus、ECharts 从 jsdelivr/unpkg 加载（与 demo 一致）；如需离线可用，把三个 js 文件下载进本地 static 目录即可，页面只需改引用路径。

## 3. 解析适配层（工作量最大的模块）

### 3.1 统一数据模型

四类文件解析后全部输出同两种结构，对账与入库只认它：

```
TxnRecord:   txn_date, direction(收/支), amount(Decimal), counterparty_name_raw,
             bank(农行/中行/建行), account_no, memo, fingerprint
InvoiceRecord: direction(销项/进项), invoice_no, invoice_date,
             seller_name, buyer_name, amount, tax, total(价税合计), items[]
```

### 3.2 每源一个 Parser（共 4 个）

| Parser | 真实数据探测结论 → 处理要点 |
|--------|------------------------------|
| 农行 .xlsx | 表头在第 4 行，前 3 行是账号/户名/币种元信息 → 跳行读取，从元信息行提取账号户名；列：交易时间/收入金额/支出金额/对方户名/摘要等 |
| 中行 .xls | 9 个 sheet，主表「详单」42 列中英双语列名；金额带千分位逗号 → 只取「详单」sheet，按列名前缀匹配取数（付款人/收款人两套列按交易类型「来账/往账」取对方）；金额字符串去逗号转 Decimal |
| 建行 .xls | 表头干净但 37 列大量冗余 → 显式列清单取 14 个有效列（账号/交易时间/借贷发生额/余额/对方户名/对方账号/摘要/记账日期等） |
| 发票 .xlsx | 多 sheet；一票多行明细（同一发票号多行商品）→ 取「发票基础信息」sheet，按「数电发票号码+销方识别号」聚合金额，明细行收纳进 items[]；销方=本公司→销项，否则进项 |

通用规则：

- **去重指纹**：银行流水 = `hash(银行 + 账号 + 交易日期 + 金额 + 方向 + 摘要 + 对方户名)`；发票 = `hash(方向 + 数电发票号码 + 销方识别号 + 价税合计)`。入库列建唯一索引，重复上传直接跳过并计数提示。
- **金额一律转 Decimal**，禁止 float 中转。
- **日期统一 ISO 格式**（各银行格式不同：农行 `2019-11-28 11:55`、中行 `20161228`、建行 `20160107 00:28`）。
- **公司主体识别**：购方或销方名称包含 SELF_KEYWORDS / 税号命中 SELF_TAX_IDS 即本方，据此判定应收（销项）/应付（进项）与流水方向。

### 3.3 导入流程（解析 → 预览 → 确认 → 事务入库）

```
上传文件 → parser 解析 → 返回前50行预览 + 总行数 + 重复行数 + 列映射说明
         → 用户确认 → 单事务批量入库（重复行自动跳过）→ 写 import_log
         → 中途取消则不入库（解析结果只存内存，不落临时表）
```

数据量小（全量约 1.2 万行），不需要异步任务队列，同步接口 + 前端 loading 即可。

## 4. 数据库设计（六张核心表）

```
counterparty        往来单位：id, name, type(客户/供应商), tax_id, remark
counterparty_alias  别名对照：id, counterparty_id(FK), alias —— 解决"简称/错别字"
invoice             发票：id, direction, invoice_no, invoice_date, seller_name,
                    buyer_name, amount DECIMAL(16,2), tax, total, item_count,
                    source(import/manual), fingerprint(唯一索引), created_at
bank_txn            流水：id, bank, account_no, txn_date, direction, amount,
                    counterparty_name_raw, counterparty_id(FK,可空), memo,
                    fingerprint(唯一索引), source, created_at
reconciliation      核销记录（append-only 事件表）：id, invoice_id(FK), txn_id(FK),
                    amount DECIMAL(16,2), method(auto/manual), status(active/reversed),
                    created_by, created_at, reversed_at, reverse_of(FK,可空)
import_log          导入记录：id, filename, parser_type, total_rows, dup_rows, imported_rows, imported_at
operation_log       操作留痕：id, table_name, record_id, action(create/update/delete),
                    diff_json, operator, created_at
```

关键设计：

- **发票一票多行**：明细不单独建表，聚合后存主表（金额/税额/价税合计），`item_count` 留行数，需要时回原始文件查。MVP 够用。
- **核销表 append-only**：撤销核销 = 插一条 `status=reversed` 记录并回填 `reverse_of` 指向被撤销的原记录，不删不改。天然满足"可撤销、可追溯"。
- **发票/流水已核销金额不落库**，由 reconciliation 表实时 SUM 得出（`已核销 = SUM(active记录.amount)`），避免删除联动时数据不一致——删除已核销单据时事务内自动插入反向核销记录。
- **往来单位匹配**：流水与发票入库时按 `counterparty_alias` 表尝试自动归属，匹配不到就挂 raw 名称，人工在台账页认领。

## 5. 对账引擎

- **触发**：手动点「重新对账」（MVP 不做增量自动触发，避免隐性状态）。
- **匹配规则（MVP 三条，由简到繁）**：
  1. 自动核销：同一往来单位 + 未核销发票余额 = 流水未核销金额（精确相等）→ 直接生成 active 核销记录；
  2. 候选推荐：金额近似（±5% 或可整除的多票组合）或部分付款 → 列出候选对，人工勾选确认；
  3. 手工指定：用户在界面上任选一笔流水 + 一张发票 + 金额，直接核销（兜底）。
- **一对多**：一笔流水可核销多张发票（分次生成多条核销记录，各记录金额之和 ≤ 流水金额）；一张发票可被多笔流水核销（部分回款）。引擎层只校验"两侧未核销余额 ≥ 本次核销金额"。
- 引擎为纯函数：输入未核销发票列表 + 未核销流水列表，输出自动匹配对 + 候选对，不改库；确认动作走 API 事务写核销记录。

## 6. 前端结构（无构建，CDN 加载）

```
static/
  index.html          入口 + 路由锚点（hash 路由，#/dashboard /#/invoice ...）
  js/
    app.js            Vue 应用入口、路由表、公共状态（axios 实例）
    api.js            接口封装
    dashboard.js      总览看板（ECharts：余额卡片/趋势/欠款TOP）
    invoice.js        发票列表（筛选/分页/新增/编辑/删除）
    txn.js            流水列表（同上）
    counterparty.js   往来单位台账（含别名管理）
    reconcile.js      对账工作台（自动匹配结果/候选勾选/手工指定/核销记录）
    report.js         余额表（下钻）/账龄分析
    import.js         导入（上传/预览/确认）
```

- 每页一个 JS 文件、一个 Vue 组件对象，`index.html` 里 `<script>` 顺序引入；无 SFC、无 JSX，模板写在组件 `template` 字符串里。
- Element Plus 用 CDN 全局版 + 其对应 CSS；图标用 element-plus-icons CDN。
- HTTP 用 axios（CDN）。
- hash 路由自己写 ~30 行（监听 hashchange 切组件），不引入 vue-router 也可；若引入 vue-router 也用 CDN 全局版，二选一，实现时定。

## 7. API 概要

```
POST /api/import/parse        上传文件 → 解析预览（不入库）
POST /api/import/commit       确认入库（事务 + 去重 + import_log）
GET  /api/invoices            列表（筛选/分页）；POST/PUT/DELETE 增删改
GET  /api/txns                同上
GET/POST/PUT/DELETE /api/counterparties (+ /api/counterparties/{id}/aliases)
POST /api/reconcile/run       跑对账引擎 → 自动核销结果 + 候选
POST /api/reconcile/confirm   确认候选核销 / 手工指定核销
POST /api/reconcile/{id}/reverse  撤销核销
GET  /api/reports/balance     应收/应付余额表（可下钻参数）
GET  /api/reports/aging       账龄分析
GET  /api/reports/trend       月度收付款趋势
GET  /api/export/{report|invoices|txns}   导出 xlsx/csv（StreamingResponse）
GET  /api/logs/operations     操作日志查询
```

统一响应体 `{code, msg, data}`；删除接口带二次确认由前端弹窗承担，后端校验核销关联并联动。

## 8. 导出

- xlsx：openpyxl 建 Workbook，余额表/账龄导出固定两张 sheet（分单位汇总 + 明细）；表头加粗、金额列数字格式 `#,##0.00`、列宽自适应（按表头长度估算）。
- csv：标准库 csv + utf-8-sig BOM（保证 Excel 打开中文不乱码）。
- 文件名规则：`{报表类型}_{筛选摘要}_{YYYYMMDD}.xlsx`；导出范围 = 当前页面筛选条件（前端把筛选参数原样传给导出接口）。

## 9. 部署与备份

- 目录：`D:\1项目\app\`（后端）+ `D:\1项目\app\static\`（前端），venv 复用 `C:\Users\drama\.workbuddy\binaries\python\envs\default` 或独立建。
- 启动：`start.bat` → `uvicorn app.main:app --host 0.0.0.0 --port 8000`（局域网可访问）。
- 备份：Windows 计划任务每日 23:00 执行 `mysqldump --single-transaction` 导出 `backup\stang_YYYYMMDD.sql`，保留最近 30 份（脚本自动清理）。
- 环境：`config.py` 读 `.env`（数据库连接、公司主体名称关键词），不硬编码。

## 10. 里程碑（对齐产品文档 M1–M5）

| 阶段 | 内容 | 验收 |
|------|------|------|
| M1 解析验证 | 4 个 parser + 统一模型，脚本直跑真实文件 | 4 文件全量解析无报错，行数与源文件一致，金额抽验 20 笔全对 |
| M2 库表与导入 | 建表、导入 API、去重、预览确认流程、前端导入页 | 全量入库，重复上传 0 新增 |
| M3 数据管理 | 发票/流水/台账增删改 + 联动 + 操作留痕 + 前端三页 | 手工改动后余额联动正确，日志可查 |
| M4 对账 | 引擎三条规则 + 工作台前端 + 撤销 | 真实数据跑通，抽验 10 组核销准确 |
| M5 报表与导出 | 余额/账龄/趋势 + xlsx/csv 导出 + 备份脚本 | 达成产品文档第 7 节验收标准 |

## 11. 风险与预留

- **中行 xls 手工对账 sheet**（美河对账/与启航往来/与五冶往来）：M1 顺带解析输出 CSV 备查，作为对账结果的对照样本，不建表。
- **Excel 模板变更**（银行改版）：parser 配置（列名映射）外置到 `parsers/config.py`，改映射不改逻辑。
- **多人版预留**：MySQL + API 架构已满足；届时只需加登录鉴权（FastAPI 依赖注入）与 operator 字段实名化，核销/操作表已含 operator 列。
- **CDN 不可达**：三个前端依赖下载一份放 static/vendor/ 作为兜底，切换只改 index.html 引用。
