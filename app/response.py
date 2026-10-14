# -*- coding: utf-8 -*-
"""统一响应体 {code, msg, data} 与异常处理。"""
from typing import Any, Optional

from fastapi import Request, FastAPI
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException


def ok(data: Any = None, msg: str = "ok") -> dict:
    return {"code": 0, "msg": msg, "data": data}


def fail(msg: str, code: int = 1, data: Any = None) -> dict:
    return {"code": code, "msg": msg, "data": data}


class BizError(Exception):
    """业务异常：直接以 {code,msg,data} 形式返回前端。"""

    def __init__(self, msg: str, code: int = 1, data: Any = None):
        super().__init__(msg)
        self.msg = msg
        self.code = code
        self.data = data


def register_exception_handlers(app: FastAPI):
    @app.exception_handler(BizError)
    async def _biz(request: Request, exc: BizError):  # noqa: ANN001
        return JSONResponse(status_code=200,
                            content=fail(exc.msg, exc.code, exc.data))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):  # noqa: ANN001
        return JSONResponse(status_code=exc.status_code,
                            content=fail(str(exc.detail), exc.status_code))

    @app.exception_handler(RequestValidationError)
    async def _valid(request: Request, exc: RequestValidationError):  # noqa: ANN001
        try:
            first = exc.errors()[0]
            loc = ".".join(str(x) for x in first.get("loc", [])[1:]) or "参数"
            msg = f"{loc}：{first.get('msg', '格式不正确')}"
        except Exception:
            msg = "请求参数格式不正确"
        return JSONResponse(status_code=200, content=fail(msg, 400))

    @app.exception_handler(Exception)
    async def _all(request: Request, exc: Exception):  # noqa: ANN001
        # 打印到控制台便于排查，同时给前端一个可读提示
        import traceback
        traceback.print_exc()
        return JSONResponse(status_code=200,
                            content=fail(f"服务器内部错误：{exc}", 500))
