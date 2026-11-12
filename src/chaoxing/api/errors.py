"""统一的 API 错误模型。

四类错误各有明确出口，前端只需要认一种 JSON 形状：

============  ====================  ============================================
来源           状态码                 载荷
============  ====================  ============================================
业务规则       400 / 409              ``{"code": "...", "message": "..."}``
未登录         401                    同上（前端据此跳登录）
无权限         403                    同上（前端据此隐藏入口）
没找到         404                    同上
未预期异常     500                    ``{"code": "internal_error", ...}``
============  ====================  ============================================

``code`` 是稳定的机器可读标识（前端拿它做分支），``message`` 是给人看的中文说明 ——
两者都要有，只给一个都会让某一方难受。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)


class ApiError(Exception):
    """业务错误（会被统一转成错误模型）。

    :param code: 机器可读标识（如 ``account_not_found``）
    :param message: 给人看的说明
    :param status_code: HTTP 状态码
    :param detail: 附加信息（字段级错误等）
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail


class NotFound(ApiError):
    """资源不存在。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, status_code=status.HTTP_404_NOT_FOUND)


class Unauthorized(ApiError):
    """未登录或登录态失效。"""

    def __init__(self, message: str = "需要登录") -> None:
        super().__init__("unauthorized", message, status_code=status.HTTP_401_UNAUTHORIZED)


class Forbidden(ApiError):
    """已登录但权限不足。"""

    def __init__(self, message: str = "权限不足", *, missing: list[str] | None = None) -> None:
        super().__init__(
            "forbidden",
            message,
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"missing": missing or []},
        )


class Conflict(ApiError):
    """与既有状态冲突（重名、重复操作等）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, status_code=status.HTTP_409_CONFLICT)


def error_payload(code: str, message: str, detail: Any = None) -> dict[str, Any]:
    """构造错误载荷（**唯一**形状来源）。"""
    payload: dict[str, Any] = {"code": code, "message": message}
    if detail is not None:
        payload["detail"] = detail
    return payload


def install_error_handlers(app: FastAPI) -> None:
    """把四类错误挂到 app 上。"""

    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": error_payload(exc.code, exc.message, exc.detail)},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # 参数校验失败也是 400 业务错误的一种：把字段级信息放进 detail
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error": error_payload(
                    "invalid_request",
                    "请求参数不合法",
                    [
                        {
                            "field": ".".join(str(part) for part in item["loc"]),
                            "reason": item["msg"],
                        }
                        for item in exc.errors()
                    ],
                )
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # 未预期异常要记全栈，但**不回传内部细节**给客户端
        log.exception("未处理异常：%s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": error_payload("internal_error", "服务内部错误")},
        )
