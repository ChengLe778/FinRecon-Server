# -*- coding: utf-8 -*-
"""SQLite → MySQL 全量迁移。

用法：
    python scripts/migrate_to_mysql.py --password 你的密码
    python scripts/migrate_to_mysql.py --host 127.0.0.1 --port 3306 \
        --user root --password 123456 --database ar_ap

可选参数：
    --sqlite   源库路径（默认 data/app.db）
    --drop     先清空目标库里的业务表（重跑用，会删数据！）
    --batch    每批写入行数（默认 500）
    --write-env 迁移成功后把 DATABASE_URL 写回 .env（自动备份 .env）
    --verify-only 只做计数校验，不写入

设计要点：
    1. 保留主键 id：核销表靠 invoice_id / txn_id 引用，id 一变关系就断。
    2. 按外键依赖顺序写入（Base.metadata.sorted_tables），导入期间关闭外键检查。
    3. 严格模式兜底：NOT NULL 空值补默认值、超长字符串按列宽截断。
    4. 写完重置 AUTO_INCREMENT，否则新增记录会撞主键。
    5. 逐表计数校验，不一致直接退出并报错。
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_SQLITE = ROOT / "data" / "app.db"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="SQLite → MySQL 数据迁移")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=3306)
    p.add_argument("--user", default="root")
    p.add_argument("--password", default="")
    p.add_argument("--database", default="ar_ap")
    p.add_argument("--sqlite", default=str(DEFAULT_SQLITE))
    p.add_argument("--drop", action="store_true", help="先清空目标库业务表")
    p.add_argument("--batch", type=int, default=500)
    p.add_argument("--write-env", action="store_true", help="迁移后写回 .env")
    p.add_argument("--verify-only", action="store_true")
    return p


def log(msg: str) -> None:
    print(msg, flush=True)


def mysql_url(a: argparse.Namespace) -> str:
    from urllib.parse import quote_plus
    pwd = quote_plus(a.password or "")
    return (f"mysql+pymysql://{quote_plus(a.user)}:{pwd}"
            f"@{a.host}:{a.port}/{a.database}?charset=utf8mb4")


def ensure_database(a: argparse.Namespace) -> None:
    import pymysql
    conn = pymysql.connect(host=a.host, port=a.port, user=a.user,
                           password=a.password or "", charset="utf8mb4",
                           connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{a.database}` "
                f"DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci")
        conn.commit()
        log(f"[OK] 目标库就绪：{a.database}（utf8mb4）")
    finally:
        conn.close()


TYPE_FALLBACK = {}


def coerce_row(table, row: dict) -> dict:
    """严格模式兜底：补 NOT NULL 空值、截断超长字符串。"""
    out = {}
    for col in table.columns:
        v = row.get(col.name)
        if v is None and not col.nullable:
            d = getattr(col, "default", None)
            if d is not None and getattr(d, "arg", None) is not None and not callable(d.arg):
                v = d.arg
            else:
                v = "" if hasattr(col.type, "length") else 0
        # 字符串超长截断（MySQL strict mode 会直接报错）
        ln = getattr(col.type, "length", None)
        if isinstance(v, str) and ln:
            if len(v) > ln:
                v = v[:ln]
        # sqlite 可能读出 int 型的布尔
        if isinstance(v, int) and hasattr(col.type, "length") is False:
            pass
        out[col.name] = v
    return out


def zero_default(col):
    from sqlalchemy import String, Integer, Numeric, Boolean, Date, DateTime
    t = col.type
    if isinstance(t, Boolean):
        return 0
    if isinstance(t, (Integer,)):
        return 0
    if isinstance(t, Numeric):
        return Decimal("0")
    if isinstance(t, DateTime):
        return datetime.now()
    if isinstance(t, Date):
        return date.today()
    return ""


def main() -> int:
    args = build_parser().parse_args()

    src_path = Path(args.sqlite)
    if not src_path.exists():
        log(f"[ERROR] 找不到源库：{src_path}")
        return 2

    try:
        import pymysql  # noqa: F401
    except ImportError:
        log("[ERROR] 缺少依赖，请先执行： pip install pymysql cryptography")
        return 2

    from sqlalchemy import create_engine, text, inspect
    from sqlalchemy.engine import make_url
    from urllib.parse import quote_plus

    src = create_engine(f"sqlite:///{src_path.as_posix()}")
    url = mysql_url(args)

    log(f"[..] 源库：{src_path}")
    log(f"[..] 目标：{args.user}@{args.host}:{args.port}/{args.database}")

    if not args.verify_only:
        ensure_database(args)

    dst = create_engine(url, pool_pre_ping=True, future=True,
                        connect_args={"connect_timeout": 10,
                                      "read_timeout": 120,
                                      "write_timeout": 120})

    # 触发模型注册（app.database 会读 .env 建一个 sqlite engine，这里只用它的 Base）
    from app.models import Base  # noqa: F401

    if not args.verify_only:
        if args.drop:
            log("[!!] --drop：清空目标库业务表")
            insp = inspect(dst)
            with dst.begin() as c:
                c.execute(text("SET FOREIGN_KEY_CHECKS=0"))
                for t in reversed(Base.metadata.sorted_tables):
                    if insp.has_table(t.name):
                        c.execute(text(f"DROP TABLE IF EXISTS `{t.name}`"))
                c.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        log("[..] 在 MySQL 建表 ...")
        Base.metadata.create_all(bind=dst)
        log("[OK] 建表完成")

    insp_dst = inspect(dst)
    order = [t for t in Base.metadata.sorted_tables]
    names = [t.name for t in order]

    log("[..] 开始迁移 " + " → ".join(names))

    total_copied = 0
    with src.connect() as s:
        for table in order:
            if not insp_dst.has_table(table.name):
                log(f"[WARN] 目标库缺表 {table.name}，跳过")
                continue
            src_ok = True
            try:
                rows = s.execute(table.select()).mappings().all()
            except Exception as e:
                log(f"[WARN] 读取源表 {table.name} 失败：{e}，跳过")
                src_ok = False
                rows = []
            n_src = len(rows) if src_ok else 0

            if args.verify_only:
                with dst.connect() as d:
                    n_dst = d.execute(text(f"SELECT COUNT(*) FROM `{table.name}`")).scalar()
                flag = "OK " if n_src == int(n_dst or 0) else "!! "
                log(f"[{flag}] {table.name:<20} 源 {n_src:>7} / 目标 {int(n_dst or 0):>7}")
                continue

            if not rows:
                log(f"[--] {table.name:<20} 源表为空")
                continue

            buf = []
            copied = 0
            with dst.begin() as d:
                d.execute(text("SET FOREIGN_KEY_CHECKS=0"))
                for r in rows:
                    buf.append(coerce_row(table, dict(r)))
                    if len(buf) >= args.batch:
                        d.execute(table.insert(), buf)
                        copied += len(buf)
                        buf.clear()
                if buf:
                    d.execute(table.insert(), buf)
                    copied += len(buf)
                    buf.clear()
                d.execute(text("SET FOREIGN_KEY_CHECKS=1"))
                # 重置自增，避免后续插入撞主键
                try:
                    mx = d.execute(text(f"SELECT MAX(id) FROM `{table.name}`")).scalar()
                    if mx:
                        d.execute(text(f"ALTER TABLE `{table.name}` AUTO_INCREMENT = {int(mx) + 1}"))
                except Exception:
                    pass
            total_copied += copied
            log(f"[OK] {table.name:<20} {copied:>7} 行")

    log(f"[OK] 迁移完成，共写入 {total_copied} 行")

    # ------------------------------- 校验
    log("[..] 计数校验")
    bad = []
    with src.connect() as s, dst.connect() as d:
        for table in order:
            try:
                n_src = s.execute(text(f"SELECT COUNT(*) FROM {table.name}")).scalar()
            except Exception:
                continue
            try:
                n_dst = d.execute(text(f"SELECT COUNT(*) FROM `{table.name}`")).scalar()
            except Exception:
                n_dst = -1
            ok = int(n_src or 0) == int(n_dst or 0)
            if not ok:
                bad.append(table.name)
            log(f"[{'OK ' if ok else '!! '}] {table.name:<20} {int(n_src or 0):>7} / {int(n_dst or 0):>7}")

    if bad:
        log("[ERROR] 以下表数量不一致：" + ", ".join(bad))
        return 1

    if args.write_env and not args.verify_only:
        env_file = ROOT / ".env"
        if env_file.exists():
            bak = ROOT / f".env.bak.{datetime.now().strftime('%Y%m%d%H%M%S')}"
            shutil.copy(env_file, bak)
            log(f"[OK] 已备份 .env → {bak.name}")
        lines = []
        if env_file.exists():
            lines = env_file.read_text(encoding="utf-8").splitlines()
        new_line = f"DATABASE_URL={url}"
        hit = False
        for i, ln in enumerate(lines):
            if ln.strip().startswith("DATABASE_URL="):
                lines[i] = new_line
                hit = True
                break
        if not hit:
            lines.append(new_line)
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log(f"[OK] .env 已更新为 MySQL：{url.replace(args.password or '', '***')}")

    log("[DONE] 现在可以启动服务： start.bat")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
