# -*- coding: utf-8 -*-
"""应收应付对账系统 · FastAPI 入口

单进程同时伺服 API 与前端静态文件，前后端同源，不存在跨域。
启动：uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from .config import STATIC_DIR, DATABASE_URL, IS_SQLITE, HOST, PORT
from .database import engine, init_db, Base
from .response import register_exception_handlers, ok, fail
from .routers import (
    import_api, invoices, txns, counterparties,
    reconcile, reports, export_api, logs,
)

app = FastAPI(
    title="应收应付对账系统",
    description="把银行流水和发票导进来，自动算出谁欠我、我欠谁",
    version="1.0.0",
)

register_exception_handlers(app)


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/api/health")
def health():
    return ok({
        "status": "running",
        "database": "sqlite" if IS_SQLITE else "mysql",
        "database_url": DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else DATABASE_URL,
        "version": "1.0.0",
    })


# ------------------------------------------------------------------ 路由
for r in (import_api.router, invoices.router, txns.router,
          counterparties.router, reconcile.router, reports.router,
          export_api.router, logs.router):
    app.include_router(r)


# ------------------------------------------------------------------ 前端
STATIC_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/")
def index():
    idx = STATIC_DIR / "index.html"
    if not idx.exists():
        return JSONResponse(status_code=200,
                            content=fail("前端文件未部署，请将 index.html 放入 app/static/", 404))
    return FileResponse(str(idx))


# 静态资源：/js /css /vendor 等
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
