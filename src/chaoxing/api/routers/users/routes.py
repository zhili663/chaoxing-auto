"""用户与角色管理端点。

到这里用户体系才闭环：注册只解决"第一个超管"，其余账号的建立与授权走这里。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from chaoxing.api.deps import AuthContext, get_container, require
from chaoxing.api.errors import ApiError, NotFound
from chaoxing.api.security import require_same_origin
from chaoxing.core.container import Container
from chaoxing.core.services.users import BUILTIN_ROLES, UserExists
from chaoxing.storage.models import User

router = APIRouter(tags=["users"])


class UserItem(BaseModel):
    """用户条目。"""

    id: int
    username: str
    display_name: str
    is_active: bool
    is_superuser: bool
    last_login_at: datetime | None = None
    roles: list[str] = Field(default_factory=list)


class UserCreate(BaseModel):
    """创建用户。"""

    username: str = Field(min_length=3, max_length=64)
    secret: str | None = Field(
        default=None,
        max_length=64,
        description="sha256(password)；留空时默认取手机号后 6 位（admin 角色必须显式设置）",
    )
    display_name: str = Field(default="", max_length=64)
    #: 手机号（可选）：留空即 ``NULL``，之后也能用手机号登录
    phone: str | None = Field(default=None, max_length=20, description="可选手机号，用于登录")
    role: str = Field(default="viewer", description="初始角色")


class RoleChange(BaseModel):
    """授权 / 收权。"""

    role: str = Field(min_length=1, max_length=32)


class OkOut(BaseModel):
    """通用回执。"""

    ok: bool = True
    message: str = ""


async def to_user_item(user: User, container: Container) -> UserItem:
    """转响应模型（附角色名，前端要展示"这人是什么角色"）。"""
    from sqlalchemy import select

    from chaoxing.storage.models import Role, user_roles

    async with container.uow.read() as db:
        rows = list(
            (
                await db.scalars(
                    select(Role.name)
                    .select_from(user_roles)
                    .join(Role, Role.id == user_roles.c.role_id)
                    .where(user_roles.c.user_id == user.id)
                )
            ).all()
        )
    return UserItem(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        last_login_at=user.last_login_at,
        roles=[str(name) for name in rows],
    )


@router.get("/users")
async def list_users(
    ctx: AuthContext = Depends(require("user:read")),
    container: Container = Depends(get_container),
) -> list[UserItem]:
    """列出全部用户（仅管理员）。"""
    users = await container.users.list_users()
    return [await to_user_item(user, container) for user in users]


@router.get("/roles")
async def list_roles(
    ctx: AuthContext = Depends(require("role:read")),
) -> dict[str, list[str]]:
    """列出内置角色与它们的权限节点（前端渲染授权下拉）。"""
    return {name: list(nodes) for name, nodes in BUILTIN_ROLES.items()}


@router.post("/users")
async def create_user(
    payload: UserCreate,
    request: Request,
    ctx: AuthContext = Depends(require("user:write")),
    container: Container = Depends(get_container),
) -> UserItem:
    """管理员创建用户（可直接指定初始角色）。"""
    require_same_origin(request)
    if not payload.secret and payload.role == "admin":
        raise ApiError(
            "invalid_secret", "管理员账号必须显式设置初始口令（不套用手机号后 6 位默认）"
        )
    user = await _create_user(container, payload)
    if payload.role:
        await container.users.grant(user.id, payload.role)
    return await to_user_item(user, container)


async def _create_user(container: Container, payload: UserCreate) -> User:
    """建号并把两类异常翻成 HTTP 错误（路由里只留一行调用）。"""
    from chaoxing.api.errors import Conflict

    try:
        return await container.users.create(
            username=payload.username,
            client_secret=payload.secret,
            display_name=payload.display_name,
            is_superuser=False,
            phone=payload.phone,
        )
    except UserExists as exc:
        raise Conflict("user_exists", str(exc)) from exc
    except ValueError as exc:
        raise ApiError("invalid_secret", str(exc)) from exc


async def _user_or_404(user_id: int, container: Container) -> User:
    """取用户或抛 404。"""
    user = await container.users.get(user_id)
    if user is None:
        raise NotFound("user_not_found", f"用户 {user_id} 不存在")
    return user


@router.post("/users/{user_id}/roles")
async def grant_role(
    user_id: int,
    payload: RoleChange,
    request: Request,
    ctx: AuthContext = Depends(require("role:manage")),
    container: Container = Depends(get_container),
) -> UserItem:
    """给用户授予角色。"""
    require_same_origin(request)
    user = await _user_or_404(user_id, container)
    await container.users.grant(user_id, payload.role)
    return await to_user_item(user, container)


@router.delete("/users/{user_id}/roles/{role}")
async def revoke_role(
    user_id: int,
    role: str,
    request: Request,
    ctx: AuthContext = Depends(require("role:manage")),
    container: Container = Depends(get_container),
) -> UserItem:
    """收回用户的角色。"""
    require_same_origin(request)
    user = await _user_or_404(user_id, container)
    await container.users.revoke(user_id, role)
    return await to_user_item(user, container)
