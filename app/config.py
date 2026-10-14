# -*- coding: utf-8 -*-
"""系统配置：从 .env 读取，不硬编码。

数据库支持 MySQL 8.x 与 SQLite 两种后端，通过 DATABASE_URL 切换：
  MySQL  : mysql+pymysql://user:pass@host:3306/ar_ap?charset=utf8mb4
  SQLite : sqlite:///<项目根目录>/data/app.db
"""
import os
import sys
from pathlib import Path
from decimal import Decimal


def _is_frozen() -> bool:
    """PyInstaller 打包后运行返回 True"""
    return getattr(sys, "frozen", False)


# 运行目录：打包后为 exe 所在目录；源码运行为项目根目录
# 数据库、.env、备份等都放这里
if _is_frozen():
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent          # 项目根目录

# 资源目录：打包后静态文件在 _MEIPASS 临时解压目录；源码运行在 app/static
if _is_frozen():
    APP_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR)) / "app"  # type: ignore
else:
    APP_DIR = BASE_DIR / "app"
STATIC_DIR = APP_DIR / "static"        # type: ignore
DATA_DIR = BASE_DIR / "data"
BACKUP_DIR = BASE_DIR / "backup"
UPLOAD_DIR = DATA_DIR / "uploads"

for _d in (DATA_DIR, BACKUP_DIR, UPLOAD_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _load_env():
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


# ---------------- 数据库 ----------------
# 默认 SQLite：开箱即用、零安装；切到 MySQL 只需改 .env 里的 DATABASE_URL
DATABASE_URL = _env("DATABASE_URL", f"sqlite:///{DATA_DIR / 'app.db'}")
IS_SQLITE = DATABASE_URL.startswith("sqlite")
IS_MYSQL = not IS_SQLITE
DB_ECHO = _env("DB_ECHO", "false").lower() == "true"

# ---------------- 业务 ----------------
# 本方主体名称关键词：用于判定销项/进项、应收/应付，以及流水的对方户名
# 默认值仅为示例，请在本目录 .env 中改成自己的主体名称/税号
SELF_KEYWORDS = [k.strip() for k in _env("SELF_KEYWORDS", "示例科技").split(",") if k.strip()]
# 本方纳税人识别号（精确识别，优先级高于关键词）
SELF_TAX_IDS = [t.strip() for t in _env("SELF_TAX_IDS", "91110108MA0EXAMPLE").split(",") if t.strip()]

# 默认操作人（MVP 单人使用，多人版时改为登录态）
DEFAULT_OPERATOR = _env("DEFAULT_OPERATOR", "admin")

# 银行名称规范
BANK_ABC = "农业银行"
BANK_BOC = "中国银行"
BANK_CCB = "建设银行"

# ---------------- 对账引擎参数 ----------------
# ---- 规则 1：自动核销（严格）----
# 自动核销允许的最大绝对金额偏差，默认 0 = 必须分文不差。
# 公司名相同但金额不等 → 一律不自动核销，只进「差异池」等待人工处理。
AUTO_AMOUNT_TOLERANCE = Decimal(_env("AUTO_AMOUNT_TOLERANCE", "0"))
# 自动核销的日期窗口（天）：流水可早于开票 N 天（预付/预收），可晚于开票 M 天
AUTO_DAYS_BEFORE = int(_env("AUTO_DAYS_BEFORE", "30"))
AUTO_DAYS_AFTER = int(_env("AUTO_DAYS_AFTER", "400"))

# ---- 规则 2：差异池推荐 ----
# 候选推荐：金额相对偏差阈值
CANDIDATE_TOLERANCE = Decimal(_env("CANDIDATE_TOLERANCE", "0.05"))
# 候选推荐：仅推荐流水日期不早于发票日期 N 天以上的组合（避免明显不相关）
CANDIDATE_MAX_DAYS_BEFORE_INVOICE = int(_env("CANDIDATE_MAX_DAYS_BEFORE_INVOICE", "400"))
# 一笔流水对多张发票时，最多组合几张发票
CANDIDATE_MAX_COMBO = int(_env("CANDIDATE_MAX_COMBO", "3"))
# 候选推荐返回的条数上限
CANDIDATE_LIMIT = int(_env("CANDIDATE_LIMIT", "300"))

# 小额差额（手续费 / 抹零 / 汇损）判定：同时满足金额 ≤ N 元 且 占比 ≤ R 才视为「小额差异」
DIFF_SMALL_AMOUNT = Decimal(_env("DIFF_SMALL_AMOUNT", "50"))
DIFF_SMALL_RATIO = Decimal(_env("DIFF_SMALL_RATIO", "0.01"))
# 差异池返回条数上限
DIFF_LIMIT = int(_env("DIFF_LIMIT", "500"))

# ---------------- 服务 ----------------
HOST = _env("HOST", "0.0.0.0")
PORT = int(_env("PORT", "8000"))

# ---------------- 账龄分档（天） ----------------
AGING_BUCKETS = [
    ("0-30", 0, 30),
    ("31-60", 31, 60),
    ("61-90", 61, 90),
    ("91-180", 91, 180),
    ("181-365", 181, 365),
    ("365+", 366, 10 ** 6),
]

# 金额精度
MONEY_QUANT = Decimal("0.01")
