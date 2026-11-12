"""审计中间件：把**写操作**记进 ``audit_logs``（行为审计通道）。

三条判断：

* 只记写操作（``POST/PATCH/PUT/DELETE``）—— GET 量大且无副作用，记了只会淹没真正重要的行；
* 失败也要记（403/409/500 正是最需要回溯的），``result`` 字段区分 ok / denied / error；
* **绝不记录请求体** —— 里面可能有明文口令。只记"谁、对哪个资源、做了什么、结果如何"。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from chaoxing.core.container import Container
from chaoxing.storage.base import utcnow
from chaoxing.storage.models import AuditLog

log = logging.getLogger(__name__)

#: 只审计这些方法（读操作不记，避免把审计表刷成流水账）
AUDITED_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})

#: 不需要审计的路径（登录/登出/健康检查：要么没有登录身份，要么噪声太大）
SKIPPED_SUFFIXES = ("/auth/login", "/auth/logout", "/health")


def action_of(request: Request) -> str:
    """由方法与路径推出动作码，例如 ``POST /api/accounts`` → ``accounts.create``。

    动作码是给人查的（``audit_logs.action`` 有索引），因此用"资源.动词"这种稳定形状，
    而不是把整条路径塞进去。
    """
    parts = [item for item in request.url.path.strip("/").split("/") if item]
    if parts and parts[0] == "api":
        parts = parts[1:]
    if not parts:
        return f"{request.method.lower()}.unknown"

    resource = parts[0]
    tail = parts[-1].lower() if len(parts) > 1 else ""
    verb = {
        "POST": "create"
        if not tail.isdigit() and tail not in {"scan", "points", "select", "run"}
        else tail,
        "PATCH": "update",
        "PUT": "update",
        "DELETE": "delete",
    }.get(request.method, request.method.lower())
    return f"{resource}.{verb or 'write'}"


def result_of(status_code: int) -> str:
    """把状态码映射成审计结果。"""
    if status_code < 400:
        return "ok"
    if status_code in {401, 403}:
        return "denied"
    return "error"


class AuditMiddleware(BaseHTTPMiddleware):
    """写操作审计（**不阻塞**：落库失败只记日志，绝不影响接口返回）。"""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """包一层：给每个请求分配 ``request_id``，写操作结束后落一条审计。"""
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        response = await call_next(request)
        response.headers["x-request-id"] = request_id

        if request.method in AUDITED_METHODS and not request.url.path.endswith(SKIPPED_SUFFIXES):
            try:
                await self._record(request, response, request_id)
            except Exception:
                log.exception("审计写入失败：%s %s", request.method, request.url.path)
        return response

    async def _record(self, request: Request, response: Response, request_id: str) -> None:
        """写一条审计（用户身份从会话里解出来；匿名也算一条）。"""
        container: Container = request.app.state.container
        token = request.cookies.get("cx_session", "")
        user = await container.users.resolve_session(token) if token else None

        async with container.uow.begin() as db:
            db.add(
                AuditLog(
                    user_id=user.id if user is not None else None,
                    action=action_of(request),
                    resource=request.url.path,
                    resource_id="",
                    result=result_of(response.status_code),
                    ip=(request.client.host if request.client else "")[:64],
                    user_agent=request.headers.get("user-agent", "")[:255],
                    request_id=request_id,
                    detail={"method": request.method, "status": response.status_code},
                    created_at=utcnow(),
                )
            )
            await db.flush()
