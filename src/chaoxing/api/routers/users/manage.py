"""用户的启用 / 删除与会话查询。

与 ``routes.py`` 的分工：那里是"有哪些用户、谁能当什么角色"（列表与授权），
这里是"某个用户的账号状态与它登录过哪里"。
拆文件的依据是 §5.11 的单文件 ≤200 行 —— 用户域的动作已经放不下一屏。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from chaoxing.api.deps import AuthContext, get_container, require
from chaoxing.api.errors import ApiError, Conflict, Forbidden
from chaoxing.api.routers.users.routes import OkOut, UserItem, _user_or_404, to_user_item
from chaoxing.api.security import require_same_origin
from chaoxing.core.container import Container
from chaoxing.core.services.users import UserExists

router = APIRouter(tags=["users"])


class UserUpdate(BaseModel):
    """可改字段（``None`` = 这一项不动）。"""

    is_active: bool | None = None
    display_name: str | None = Field(default=None, max_length=64)
    #: 手机号（``None`` = 不动；非空时不能被别的账号占用）
    phone: str | None = Field(default=None, max_length=20)


class SessionItem(BaseModel):
    """一条会话（**不含 token**：库里只有指纹，这里也不回显）。"""

    id: int
    ip: str
    user_agent: str
    created_at: datetime
    last_seen_at: datetime | None = None
    revoked_at: datetime | None = None


@router.patch("/users/{user_id}")
async def update_user(
    user_id: int,
    payload: UserUpdate,
    request: Request,
    ctx: AuthContext = Depends(require("user:write")),
    container: Container = Depends(get_container),
) -> UserItem:
    """改启用状态 / 展示名 / 手机号（**不能停用自己** —— 否则会把自己锁在门外）。"""
    require_same_origin(request)
    await _user_or_404(user_id, container)
    if user_id == ctx.user.id and payload.is_active is False:
        raise ApiError("cannot_modify_self", "不能停用当前登录的账号")
    if payload.phone is not None:
        try:
            await container.users.set_phone(user_id, payload.phone)
        except UserExists as exc:
            raise Conflict("user_exists", str(exc)) from exc
    user = await container.users.update(
        user_id, is_active=payload.is_active, display_name=payload.display_name
    )
    return await to_user_item(user or await _user_or_404(user_id, container), container)


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    request: Request,
    ctx: AuthContext = Depends(require("user:write")),
    container: Container = Depends(get_container),
) -> OkOut:
    """删除用户（会话级联删除；不能删自己，删超管要求调用者也是超管）。"""
    require_same_origin(request)
    user = await _user_or_404(user_id, container)
    if user.id == ctx.user.id:
        raise ApiError("cannot_modify_self", "不能删除当前登录的账号")
    if user.is_superuser and not ctx.user.is_superuser:
        raise Forbidden("只有超管能删除超管账号")
    await container.users.delete(user_id)
    return OkOut(message=f"已删除用户 {user.username}")


@router.get("/users/{user_id}/sessions")
async def list_user_sessions(
    user_id: int,
    ctx: AuthContext = Depends(require("user:read")),
    container: Container = Depends(get_container),
) -> list[SessionItem]:
    """列出某用户的会话（含已撤销，按创建时间倒序）—— 排障"谁在用这个账号"。"""
    await _user_or_404(user_id, container)
    rows = await container.users.list_sessions(user_id)
    return [
        SessionItem(
            id=row.id,
            ip=row.ip,
            user_agent=row.user_agent,
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
            revoked_at=row.revoked_at,
        )
        for row in rows
    ]


__all__ = ["router"]
