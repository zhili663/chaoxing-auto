"""账号读写端点。

数据可见性落在两处：列表按 ``visible_owner`` 过滤；单条走 :func:`load_account`
做归属校验（无权访问时返回 404 而不是 403，不泄露账号是否存在）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from chaoxing.api.deps import AuthContext, get_container, load_account, require, visible_owner
from chaoxing.api.errors import Conflict
from chaoxing.api.routers.accounts.schemas import (
    AccountCreate,
    AccountOut,
    AccountUpdate,
    LoginRequest,
    LoginResultOut,
)
from chaoxing.api.security import require_same_origin
from chaoxing.core.container import Container
from chaoxing.core.services.accounts import AccountExists
from chaoxing.storage.models import Account

router = APIRouter(tags=["accounts"])


def to_account_out(account: Account) -> AccountOut:
    """转响应模型（凭据一律不外发，只给"有没有"两个布尔值）。"""
    return AccountOut(
        id=account.id,
        username=account.username,
        owner_user_id=account.owner_user_id,
        enabled=account.enabled,
        priority=account.priority,
        note=account.note,
        status=account.status,
        has_password=bool(account.password_enc),
        has_session=bool(account.cookie_enc),
        last_check_at=account.last_check_at,
    )


@router.get("/accounts")
async def list_accounts(
    group_id: int | None = None,
    ctx: AuthContext = Depends(require("account:read")),
    container: Container = Depends(get_container),
) -> list[AccountOut]:
    """列出可见账号（管理员看全部，普通用户只看自己的）。

    ``group_id`` 可再按虚拟账户组收窄 —— 但**组不放松可见性**：
    两个过滤条件是叠加的（先按归属、再按组），不是二选一。
    """
    accounts = await container.accounts.list_accounts(
        owner_user_id=visible_owner(ctx), group_id=group_id
    )
    return [to_account_out(item) for item in accounts]


@router.get("/accounts/{account_id}")
async def get_account(
    account_id: int,
    ctx: AuthContext = Depends(require("account:read")),
    container: Container = Depends(get_container),
) -> AccountOut:
    """账号详情。"""
    account = await load_account(account_id, ctx, container)
    return to_account_out(account)


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
async def create_account(
    payload: AccountCreate,
    request: Request,
    ctx: AuthContext = Depends(require("account:write")),
    container: Container = Depends(get_container),
) -> AccountOut:
    """新增账号（密码经 Fernet 加密落库）。

    归属规则：**默认归创建者**；管理员可显式传 ``owner_user_id``（传 ``null`` 即共享账号）。
    不这么做的话，管理员建的账号会静默变成"所有人可见的共享账号" ——
    而这类账号本该是显式选择的产物。
    """
    require_same_origin(request)
    owner: int | None = ctx.user.id
    if ctx.sees_everything and "owner_user_id" in payload.model_fields_set:
        owner = payload.owner_user_id

    try:
        account = await container.accounts.create_account(
            username=payload.username,
            password=payload.password,
            owner_user_id=owner,
            priority=payload.priority,
            note=payload.note,
        )
    except AccountExists as exc:
        raise Conflict("account_exists", str(exc)) from exc
    return to_account_out(account)


@router.patch("/accounts/{account_id}")
async def update_account(
    account_id: int,
    payload: AccountUpdate,
    request: Request,
    ctx: AuthContext = Depends(require("account:write")),
    container: Container = Depends(get_container),
) -> AccountOut:
    """更新账号（只改传进来的字段）。"""
    require_same_origin(request)
    await load_account(account_id, ctx, container)

    async with container.uow.begin() as db:
        account = await db.get(Account, account_id)
        assert account is not None  # 上面已校验过
        if payload.password is not None:
            account.password_enc = container.box.encrypt(payload.password)
        if payload.note is not None:
            account.note = payload.note
        if payload.priority is not None:
            account.priority = payload.priority
        if payload.enabled is not None:
            account.enabled = payload.enabled
        await db.flush()
        return to_account_out(account)


@router.delete("/accounts/{account_id}")
async def delete_account(
    account_id: int,
    request: Request,
    ctx: AuthContext = Depends(require("account:delete")),
    container: Container = Depends(get_container),
) -> dict[str, bool]:
    """删除账号（关联的课程与任务点按外键级联清理）。"""
    require_same_origin(request)
    await load_account(account_id, ctx, container)

    async with container.uow.begin() as db:
        account = await db.get(Account, account_id)
        if account is not None:
            await db.delete(account)
            await db.flush()
    await container.accounts.delete_account(account_id)
    return {"ok": True}


@router.post("/accounts/{account_id}/login")
async def login_account(
    account_id: int,
    payload: LoginRequest,
    request: Request,
    ctx: AuthContext = Depends(require("account:login")),
    container: Container = Depends(get_container),
) -> LoginResultOut:
    """触发超星账号登录（密码可选：不传则用库里已存的）。"""
    require_same_origin(request)
    await load_account(account_id, ctx, container)

    result = await container.accounts.login(
        account_id, password=payload.password, captcha=payload.captcha
    )
    return LoginResultOut(
        ok=result.ok,
        message=result.message,
        reason=result.reason,
        need_human=result.need_human,
    )
