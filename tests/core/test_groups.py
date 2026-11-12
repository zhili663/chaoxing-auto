"""虚拟账户组：建组、加成员、移出、删组。

一条关键的性质要用测试钉住：**加入组不改变账号归属** ——
组只是"视图与批量入口"，可见性仍由账号自己的属主决定。
否则"共享"就变成了"悄悄把别人的账号交出去"。
"""

from __future__ import annotations

from sqlalchemy import select

from chaoxing.core.services.groups import GroupExists, GroupNotFound, GroupService
from chaoxing.storage.models import Account, AccountGroup
from chaoxing.storage.uow import UnitOfWork


async def make_account(uow: UnitOfWork, username: str) -> int:
    """造一个账号。"""
    async with uow.begin() as session:
        account = Account(username=username)
        session.add(account)
        await session.flush()
        return account.id


async def test_create_and_list_groups(uow: UnitOfWork) -> None:
    """建组后能在概览里看到，成员数为 0。"""
    service = GroupService(uow=uow)

    await service.create(name="机房A", note="三楼")

    rows = await service.overview()
    assert len(rows) == 1
    assert rows[0].name == "机房A"
    assert rows[0].members == 0


async def test_duplicate_name_is_rejected(uow: UnitOfWork) -> None:
    """同名组（同一属主）不能再建。"""
    service = GroupService(uow=uow)
    await service.create(name="机房A")

    try:
        await service.create(name="机房A")
    except GroupExists:
        return
    raise AssertionError("重名应当被拒绝")


async def test_add_and_remove_members(uow: UnitOfWork) -> None:
    """加成员 → 计数正确 → 移出 → 计数归零。"""
    service = GroupService(uow=uow)
    first = await make_account(uow, "13800000001")
    second = await make_account(uow, "13800000002")
    group = await service.create(name="机房A")

    added = await service.add_accounts(group.id, [first, second])
    assert added == 2
    assert sorted(await service.member_ids(group.id)) == sorted([first, second])
    assert (await service.overview())[0].members == 2

    removed = await service.remove_accounts(group.id, [first])
    assert removed == 1
    assert await service.member_ids(group.id) == [second]


async def test_adding_twice_is_idempotent(uow: UnitOfWork) -> None:
    """重复加入不报错也不重复计数（批量操作要能反复跑）。"""
    service = GroupService(uow=uow)
    account_id = await make_account(uow, "13800000003")
    group = await service.create(name="机房A")

    assert await service.add_accounts(group.id, [account_id]) == 1
    assert await service.add_accounts(group.id, [account_id]) == 0
    assert await service.member_ids(group.id) == [account_id]


async def test_unknown_account_is_skipped(uow: UnitOfWork) -> None:
    """加入不存在的账号要跳过（外键会在提交时炸，错误信息还很难懂）。"""
    service = GroupService(uow=uow)
    group = await service.create(name="机房A")

    assert await service.add_accounts(group.id, [999_999]) == 0


async def test_missing_group_raises(uow: UnitOfWork) -> None:
    """对不存在的组加成员要明确报错。"""
    service = GroupService(uow=uow)

    try:
        await service.add_accounts(999, [1])
    except GroupNotFound:
        return
    raise AssertionError("组不存在时应当抛 GroupNotFound")


async def test_group_membership_does_not_change_account_ownership(uow: UnitOfWork) -> None:
    """**加入组不改变账号归属** —— 这是整组设计的安全底线。"""
    service = GroupService(uow=uow)
    async with uow.begin() as session:
        account = Account(username="13800000004", owner_user_id=None)
        session.add(account)
        await session.flush()
        account_id = account.id

    group = await service.create(name="机房A")
    await service.add_accounts(group.id, [account_id])

    async with uow.read() as session:
        stored = (await session.scalars(select(Account).where(Account.id == account_id))).one()
    assert stored.owner_user_id is None  # 归属没被动过


async def test_delete_group_keeps_accounts(uow: UnitOfWork) -> None:
    """删组不会删账号 —— 组只是视图，不是容器。"""
    service = GroupService(uow=uow)
    account_id = await make_account(uow, "13800000005")
    group = await service.create(name="机房A")
    await service.add_accounts(group.id, [account_id])

    assert await service.delete(group.id) is True
    assert await service.delete(group.id) is False  # 再删返回 False

    async with uow.read() as session:
        assert (await session.scalars(select(Account).where(Account.id == account_id))).first()
        assert (await session.scalars(select(AccountGroup))).all() == []


async def test_list_scoped_by_owner_includes_system_groups(uow: UnitOfWork) -> None:
    """按属主过滤时，**系统组永远可见**（那是大家共用的那批）。"""
    from chaoxing.storage.models import User

    service = GroupService(uow=uow)
    async with uow.begin() as session:
        other = User(username="other")
        session.add(other)
        await session.flush()
        other_id = other.id

    await service.create(name="系统组", owner_user_id=None)
    await service.create(name="别人的组", owner_user_id=other_id)

    mine = await service.list_groups(owner_user_id=other_id + 1)

    assert [item.name for item in mine] == ["系统组"]
