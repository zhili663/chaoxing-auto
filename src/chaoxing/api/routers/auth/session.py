"""登录、登出与"我是谁"。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from chaoxing.api.deps import AuthContext, auth_context, get_container
from chaoxing.api.errors import Unauthorized
from chaoxing.api.routers.auth.schemas import LoginRequest, OkOut, UserOut
from chaoxing.api.security import (
    clear_session_cookie,
    read_session_cookie,
    require_same_origin,
    set_session_cookie,
)
from chaoxing.core.container import Container
from chaoxing.storage.models import User

router = APIRouter(tags=["auth"])


def to_user_out(user: User, permissions: set[str]) -> UserOut:
    """把用户与权限集合转成响应模型（权限集合一直是排序后的，便于前端 diff）。"""
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        permissions=sorted(permissions),
        phone=user.phone,
    )


@router.post("/auth/login")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    container: Container = Depends(get_container),
) -> UserOut:
    """登录并写入 HttpOnly 会话 Cookie。"""
    require_same_origin(request)

    user = await container.users.authenticate(payload.username, payload.secret)
    if user is None:
        # 不区分"用户不存在"与"口令错误"：避免给撞库提供信号
        raise Unauthorized("用户名或口令不正确")

    token = await container.users.open_session(
        user,
        ip=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", ""),
    )
    set_session_cookie(response, token, secure=container.settings.app.secure_cookies)
    return to_user_out(user, await container.users.permissions(user))


@router.post("/auth/logout")
async def logout(
    request: Request, response: Response, container: Container = Depends(get_container)
) -> OkOut:
    """登出（撤销会话并清除 Cookie）。"""
    await container.users.close_session(read_session_cookie(request))
    clear_session_cookie(response)
    return OkOut(message="已登出")


@router.get("/auth/me")
async def me(ctx: AuthContext = Depends(auth_context)) -> UserOut:
    """当前用户（未登录返回 401）。"""
    return to_user_out(ctx.user, ctx.permissions)
