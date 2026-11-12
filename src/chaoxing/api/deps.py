"""FastAPI 依赖：容器、当前用户、权限校验、数据可见性。

**每个路由都要挂鉴权**（读路由同样要挂）—— 权限节点不只管"能不能进"，
还决定**数据范围**（``account:read:all`` 才能看别人的账号）。
``tests/api/test_permissions.py`` 会遍历路由表做元测试，漏挂直接失败。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Request

from chaoxing.api.errors import Forbidden, Unauthorized
from chaoxing.api.security import read_session_cookie
from chaoxing.core.container import Container
from chaoxing.core.services.users import WILDCARD
from chaoxing.storage.models import User


def get_container(request: Request) -> Container:
    """取进程容器（lifespan 时挂在 ``app.state`` 上）。"""
    return request.app.state.container  # type: ignore[no-any-return]


@dataclass(slots=True)
class AuthContext:
    """一次请求的鉴权上下文。"""

    user: User
    permissions: set[str]

    def can(self, permission: str) -> bool:
        """是否具备某权限（超管的通配权限对所有节点成立）。"""
        return WILDCARD in self.permissions or permission in self.permissions

    @property
    def sees_everything(self) -> bool:
        """数据可见性：能否跨账号查看（管理员）。"""
        return self.can("account:read:all")


async def current_user(
    request: Request, container: Container = Depends(get_container)
) -> User | None:
    """从会话 Cookie 解析当前用户（未登录返回 ``None``）。"""
    token = read_session_cookie(request)
    if not token:
        return None
    return await container.users.resolve_session(token)


async def require_user(user: User | None = Depends(current_user)) -> User:
    """必须登录。

    :raises Unauthorized: 未登录或登录态失效
    """
    if user is None:
        raise Unauthorized()
    return user


async def auth_context(
    user: User = Depends(require_user), container: Container = Depends(get_container)
) -> AuthContext:
    """构造鉴权上下文（权限集合在这里算一次）。"""
    return AuthContext(user=user, permissions=await container.users.permissions(user))


def require(*needed: str) -> Callable[..., Any]:
    """声明路由所需权限。

    用法::

        @router.get("/accounts", dependencies=[require("account:read")])
        async def list_accounts(ctx: AuthContext = Depends(auth_context)): ...

    :param needed: 需要的权限码（**全部**满足才放行）
    :returns: 依赖函数
    """

    async def dependency(ctx: AuthContext = Depends(auth_context)) -> AuthContext:
        missing = [item for item in needed if not ctx.can(item)]
        if missing:
            raise Forbidden(f"缺少权限：{', '.join(missing)}", missing=missing)
        return ctx

    # 标记用来自查：元测试遍历路由表时靠它判断"这条路由到底要不要鉴权"
    dependency.__requires__ = tuple(needed)  # type: ignore[attr-defined]
    return dependency


def visible_owner(ctx: AuthContext) -> int | None:
    """按数据可见性算出"该看谁的账号"。

    :param ctx: 鉴权上下文
    :returns: ``None`` = 不限（管理员）；否则只看该用户自己的
    """
    return None if ctx.sees_everything else ctx.user.id


async def load_account(account_id: int, ctx: AuthContext, container: Container) -> Any:
    """取账号并做归属校验。

    非管理员访问别人的账号时返回 **404 而不是 403** —— 403 等于告诉对方"这个 ID 存在"，
    把账号存在性变成了可探测信息。对无权访问者，它就该跟不存在一样。

    :param account_id: 账号 ID
    :param ctx: 鉴权上下文
    :param container: 容器
    :raises NotFound: 不存在或无权访问
    :returns: 账号 ORM 对象
    """
    from chaoxing.api.errors import NotFound
    from chaoxing.core.services import AccountNotFound

    try:
        account = await container.accounts.get(account_id)
    except AccountNotFound as exc:
        raise NotFound("account_not_found", f"账号 {account_id} 不存在") from exc

    if not ctx.sees_everything and account.owner_user_id not in (None, ctx.user.id):
        raise NotFound("account_not_found", f"账号 {account_id} 不存在")
    return account


def optional_user() -> Callable[..., Any]:
    """允许未登录的路由用它拿"可能是 None"的用户。"""

    async def dependency(user: User | None = Depends(current_user)) -> User | None:
        return user

    return dependency
