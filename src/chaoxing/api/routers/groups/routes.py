"""账户组端点（虚拟账户组）。

组是**批量操作与共享的入口**，但它不改变账号归属 —— 账号的可见性仍由
``owner_user_id`` 决定。因此这里鉴权分两层：``group:read`` / ``group:write``
管"能不能动组"，而账号本身的归属校验仍走 :func:`load_account`
（加成员时逐个校验，组不该成为越权通道）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from chaoxing.api.deps import AuthContext, get_container, load_account, require
from chaoxing.api.errors import Conflict, NotFound
from chaoxing.api.security import require_same_origin
from chaoxing.core.container import Container
from chaoxing.core.services.groups import GroupExists, GroupNotFound, GroupOverview
from chaoxing.storage.models import AccountGroup

router = APIRouter(tags=["groups"])


class GroupItem(BaseModel):
    """组 + 成员数。"""

    id: int
    name: str
    owner_user_id: int | None
    visibility: str
    note: str
    members: int


class GroupCreate(BaseModel):
    """建组。"""

    name: str = Field(min_length=1, max_length=64)
    visibility: str = Field(default="private", pattern="^(public|private)$")
    note: str = Field(default="", max_length=255)


class MembersChange(BaseModel):
    """成员增减。"""

    account_ids: list[int] = Field(default_factory=list)


class MembersResult(BaseModel):
    """成员变更结果。"""

    changed: int
    members: int


def to_group_item(group: AccountGroup | GroupOverview, *, members: int = 0) -> GroupItem:
    """组 → 响应模型（ORM 对象与概览 dataclass 都能吃）。"""
    return GroupItem(
        id=group.id,
        name=group.name,
        owner_user_id=group.owner_user_id,
        visibility=group.visibility,
        note=group.note,
        members=members or getattr(group, "members", 0),
    )


async def _group_or_404(group_id: int, container: Container) -> AccountGroup:
    """取组（不存在则 404）。"""
    try:
        return await container.groups.get(group_id)
    except GroupNotFound as exc:
        raise NotFound("group_not_found", str(exc)) from exc


@router.get("/groups")
async def list_groups(
    ctx: AuthContext = Depends(require("group:read")),
    container: Container = Depends(get_container),
) -> list[GroupItem]:
    """列出组与成员数（系统组对所有人可见）。"""
    rows = await container.groups.overview()
    return [to_group_item(row) for row in rows]


@router.post("/groups")
async def create_group(
    payload: GroupCreate,
    request: Request,
    ctx: AuthContext = Depends(require("group:write")),
    container: Container = Depends(get_container),
) -> GroupItem:
    """建组（管理员建出来的是系统组：属主留空，所有人可用）。"""
    require_same_origin(request)
    owner = None if ctx.sees_everything else ctx.user.id
    try:
        group = await container.groups.create(
            name=payload.name,
            owner_user_id=owner,
            visibility=payload.visibility,
            note=payload.note,
        )
    except GroupExists as exc:
        raise Conflict("group_exists", str(exc)) from exc
    return to_group_item(group)


@router.post("/groups/{group_id}/members")
async def add_members(
    group_id: int,
    payload: MembersChange,
    request: Request,
    ctx: AuthContext = Depends(require("group:write")),
    container: Container = Depends(get_container),
) -> MembersResult:
    """把账号加入组（**逐个校验账号可见性** —— 组不该成为越权通道）。"""
    require_same_origin(request)
    await _group_or_404(group_id, container)
    for account_id in payload.account_ids:
        await load_account(account_id, ctx, container)

    added = await container.groups.add_accounts(group_id, payload.account_ids)
    members = await container.groups.member_ids(group_id)
    return MembersResult(changed=added, members=len(members))


@router.delete("/groups/{group_id}/members")
async def remove_members(
    group_id: int,
    payload: MembersChange,
    request: Request,
    ctx: AuthContext = Depends(require("group:write")),
    container: Container = Depends(get_container),
) -> MembersResult:
    """把账号移出组（**只解除关联，不删账号**）。"""
    require_same_origin(request)
    await _group_or_404(group_id, container)
    removed = await container.groups.remove_accounts(group_id, payload.account_ids)
    members = await container.groups.member_ids(group_id)
    return MembersResult(changed=removed, members=len(members))


@router.delete("/groups/{group_id}/members/{account_id}")
async def remove_member(
    group_id: int,
    account_id: int,
    request: Request,
    ctx: AuthContext = Depends(require("group:write")),
    container: Container = Depends(get_container),
) -> MembersResult:
    """把**一个**账号移出组（路径式）。

    与批量版并存不是重复：`DELETE` 带 body 在不少客户端/代理上会被丢掉，
    单个成员的操作走路径更稳，也更贴合"点一下移出"的界面动作。
    """
    require_same_origin(request)
    await _group_or_404(group_id, container)
    removed = await container.groups.remove_accounts(group_id, [account_id])
    members = await container.groups.member_ids(group_id)
    return MembersResult(changed=removed, members=len(members))


@router.delete("/groups/{group_id}")
async def delete_group(
    group_id: int,
    request: Request,
    ctx: AuthContext = Depends(require("group:write")),
    container: Container = Depends(get_container),
) -> dict[str, bool]:
    """删组（组里的账号不受影响）。"""
    require_same_origin(request)
    return {"removed": await container.groups.delete(group_id)}
