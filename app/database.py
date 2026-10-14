# -*- coding: utf-8 -*-
"""SQLAlchemy 引擎与会话。MySQL / SQLite 双后端，切换只改 DATABASE_URL。

历史问题（已修复）：
    1) 原实现在 SQLite 上使用 StaticPool —— 整个进程只有一条物理连接，
       被 FastAPI 的线程池并发复用。SQLAlchemy 的 C 扩展 resultproxy 在
       两个线程交错使用同一个 DBAPI cursor 时会读到错位的列处理器，
       抛出 `IndexError: tuple index out of range`，表现为
       `Exception in ASGI application`（常见于报表页多点开几个 Tab 时）。

    2) 中途改用 SingletonThreadPool 反而更糟：它按线程 ident 缓存连接，
       而线程池的 ident 在线程结束后会**被新线程复用**；同时它的
       `_cleanup()` 在连接数超限时会把连接 close 掉。压测下表现为
       `Cannot operate on a closed database`，并最终让解释器
       **Segmentation fault**（C 层 use-after-free）。

    现在的方案：QueuePool + check_same_thread=False。
    连接池保证「一条连接同一时刻只被一个线程持有」，从根上避免 cursor
    串扰（连接只会在不同时间交给不同线程，不会并发共用）；再配合 WAL +
    busy_timeout 解决写锁竞争。
"""
from __future__ import annotations

import logging
import warnings

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.pool import QueuePool

from .config import DATABASE_URL, IS_SQLITE, DB_ECHO

logger = logging.getLogger("uvicorn.error")

# 连接池容量。SQLite 是本地文件，连接成本极低，池可以开大一点；
# 但写操作需要独占写锁，靠 busy_timeout 排队即可。
_SQLITE_POOL_SIZE = 10
_SQLITE_MAX_OVERFLOW = 30
# MySQL：索引长度上限（InnoDB + utf8mb4 单列最多 3072 字节）
_MYSQL_POOL_SIZE = 10
_MYSQL_MAX_OVERFLOW = 20


def _ensure_mysql_database() -> None:
    """库不存在时自动创建（UTF8MB4）。无建库权限但库已存在时静默跳过。"""
    try:
        u = make_url(DATABASE_URL)
    except Exception:
        return
    if not u.database:
        return
    try:
        import pymysql  # type: ignore
    except ImportError:
        logger.warning("[DB] 未安装 pymysql，跳过自动建库。请执行：pip install pymysql cryptography")
        return
    conn = None
    try:
        conn = pymysql.connect(
            host=u.host or "127.0.0.1",
            port=u.port or 3306,
            user=u.username or "root",
            password=u.password or "",
            charset="utf8mb4",
            connect_timeout=8,
        )
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{u.database}` "
                f"DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
            )
        conn.commit()
        logger.info(f"[DB] MySQL 数据库已就绪：{u.database}")
    except Exception as e:
        # 库已存在但账号无 CREATE 权限时也会走到这里，不阻断启动
        logger.warning(f"[DB] 自动建库跳过（{e}）；若库不存在请手动创建")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


if IS_SQLITE:
    # ---------------------------------------------------------------- SQLite
    # QueuePool：池保证一条连接同一时刻只借给一个线程，杜绝 cursor 串扰。
    # check_same_thread=False 是必要的——连接会被线程池里的不同线程先后持有。
    # pool_pre_ping 剔除任何被意外关闭的连接（成本：一次 SELECT 1）。
    engine = create_engine(
        DATABASE_URL,
        echo=DB_ECHO,
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=QueuePool,
        pool_size=_SQLITE_POOL_SIZE,
        max_overflow=_SQLITE_MAX_OVERFLOW,
        pool_pre_ping=True,
        pool_recycle=3600,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragma(dbapi_conn, _rec):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA foreign_keys=ON")
            # WAL：读不阻塞写，写不阻塞读，显著降低 "database is locked"
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=30000")
        except Exception:
            pass
        finally:
            cur.close()
else:
    # ----------------------------------------------------------------- MySQL
    _ensure_mysql_database()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        engine = create_engine(
            DATABASE_URL,
            echo=DB_ECHO,
            future=True,
            pool_pre_ping=True,          # 自动剔除被服务端断开的空闲连接
            pool_recycle=1800,           # 小于 MySQL wait_timeout（默认 28800）
            pool_size=_MYSQL_POOL_SIZE,
            max_overflow=_MYSQL_MAX_OVERFLOW,
            poolclass=QueuePool,
            connect_args={"connect_timeout": 10, "read_timeout": 60, "write_timeout": 60},
        )

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False,
                            expire_on_commit=False, future=True)

Base = declarative_base()


def db_dialect() -> str:
    """当前数据库方言：sqlite / mysql"""
    return "sqlite" if IS_SQLITE else "mysql"


def get_db():
    """FastAPI 依赖：每个请求一个会话。"""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _sync_schema():
    """给已存在的表补上新增列。

    create_all 只建新表，不会修改已有表。老库升级到新版本时，
    invoice.category / reconciliation.diff_amount 等新列需要靠这里补上。
    """
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    tc = engine.dialect.type_compiler_instance
    for table in Base.metadata.sorted_tables:
        try:
            if not insp.has_table(table.name):
                continue
        except Exception:
            continue
        existing = {c["name"] for c in insp.get_columns(table.name)}
        missing = [c for c in table.columns if c.name not in existing]
        if not missing:
            continue
        with engine.begin() as conn:
            for col in missing:
                try:
                    col_type = tc.process(col.type)
                    conn.execute(text(
                        f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col_type}"))
                    logger.info(f"[DB] 表 {table.name} 新增列 {col.name} {col_type}")
                except Exception as e:
                    logger.warning(f"[DB] 表 {table.name} 新增列 {col.name} 失败：{e}")
            # 补列后回填默认值，避免 NOT NULL 语义下的空值
            for col in missing:
                if col.default is not None and col.default.arg is not None:
                    try:
                        conn.execute(text(
                            f"UPDATE {table.name} SET {col.name} = :v "
                            f"WHERE {col.name} IS NULL"), {"v": col.default.arg})
                    except Exception:
                        pass


def init_db():
    """建表（幂等）+ 补列。导入所有模型后调用。"""
    from . import models  # noqa: F401  确保模型注册表已填充
    Base.metadata.create_all(bind=engine)
    _sync_schema()
