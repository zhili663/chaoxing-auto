"""虚拟账户组：批量操作与共享的粒度。

为什么需要它（计划 §5.8 的设计意图）
------------------------------------
超星账号与平台用户**不强制绑定**（``owner_user_id`` 可空）。但"机房 A 的 30 个号"
这类场景需要一个**批量操作与共享的单位** —— 那就是虚拟账户组：

* 组可以属于某个用户，也可以是**系统组**（``owner_user_id`` 为空，大家都能用）；
* 账号可以同时在多个组里（关联表，不是外键外键的一对多）；
* 组只是"视图与批量入口"，**不改变账号的归属** —— 账号的可见性仍由 ``owner_user_id`` 决定，
  这样"共享"就不会变成"悄悄把别人的账号交出去"。

依赖方向：``core → storage``，与其它服务一致。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select

from chaoxing.storage.models import Account, AccountGroup, account_group_members
from chaoxing.storage.uow import UnitOfWork

log = logging.getLogger(__name__)


class GroupNotFound(Exception):
    """组不存在。"""


class GroupExists(Exception):
    """同名组已存在（同一属主下）。"""


@dataclass(slots=True)
class GroupOverview:
    """组 + 成员数的概览（界面与 CLI 都用它一次拿全）。"""

    id: int
    name: str
    owner_user_id: int | None
    visibility: str
    note: str
    members: int


class GroupService:
    """虚拟账户组的读写。

    :param uow: 工作单元
    """

    def __init__(self, *, uow: UnitOfWork) -> None:
        self._uow = uow

    # ------------------------------------------------------------------ #
    # 读
    # ------------------------------------------------------------------ #
    async def list_groups(self, *, owner_user_id: int | None = None) -> Sequence[AccountGroup]:
        """列出可见的组。

        :param owner_user_id: 只看某人的组（``None`` = 不限，管理员视角）；
            系统组（属主为空）永远可见
        :returns: 组列表（按 ID）
        """
        async with self._uow.read() as session:
            statement = select(AccountGroup).order_by(AccountGroup.id)
            if owner_user_id is not None:
                statement = statement.where(
                    (AccountGroup.owner_user_id == owner_user_id)
                    | AccountGroup.owner_user_id.is_(None)
                )
            return list((await session.scalars(statement)).all())

    async def get(self, group_id: int) -> AccountGroup:
        """取组。

        :param group_id: 组 ID
        :raises GroupNotFound: 不存在
        """
        async with self._uow.read() as session:
            group = await session.get(AccountGroup, group_id)
            if group is None:
                raise GroupNotFound(f"组 {group_id} 不存在")
            return group

    async def member_ids(self, group_id: int) -> list[int]:
        """组的成员账号 ID。"""
        async with self._uow.read() as session:
            rows = await session.execute(
                select(account_group_members.c.account_id).where(
                    account_group_members.c.group_id == group_id
                )
            )
            return [int(value) for value in rows.scalars().all()]

    async def overview(self) -> list[GroupOverview]:
        """组 + 成员数的概览（界面与 CLI 都用它一次拿全）。"""
        async with self._uow.read() as session:
            groups = list(
                (await session.scalars(select(AccountGroup).order_by(AccountGroup.id))).all()
            )
            counts = {
                int(group_id): int(total)
                for group_id, total in (
                    await session.execute(
                        select(
                            account_group_members.c.group_id,
                            func.count(account_group_members.c.account_id),
                        ).group_by(account_group_members.c.group_id)
                    )
                ).all()
            }
        return [
            GroupOverview(
                id=group.id,
                name=group.name,
                owner_user_id=group.owner_user_id,
                visibility=group.visibility,
                note=group.note,
                members=counts.get(group.id, 0),
            )
            for group in groups
        ]

    # ------------------------------------------------------------------ #
    # 写
    # ------------------------------------------------------------------ #
    async def create(
        self,
        *,
        name: str,
        owner_user_id: int | None = None,
        visibility: str = "private",
        note: str = "",
    ) -> AccountGroup:
        """建组。

        :param name: 组名（同一属主下唯一）
        :param owner_user_id: 属主（``None`` = 系统组）
        :param visibility: ``public`` / ``private``
        :param note: 备注
        :raises GroupExists: 同名组已存在
        :returns: 新建的组
        """
        async with self._uow.begin() as session:
            existing = (
                await session.scalars(
                    select(AccountGroup).where(
                        AccountGroup.name == name,
                        AccountGroup.owner_user_id.is_(None)
                        if owner_user_id is None
                        else AccountGroup.owner_user_id == owner_user_id,
                    )
                )
            ).first()
            if existing is not None:
                raise GroupExists(f"组 {name} 已存在")

            group = AccountGroup(
                name=name, owner_user_id=owner_user_id, visibility=visibility, note=note
            )
            session.add(group)
            await session.flush()
            log.info("已建组 %s（ID %s，属主 %s）", name, group.id, owner_user_id)
            return group

    async def add_accounts(self, group_id: int, account_ids: Sequence[int]) -> int:
        """把账号加入组（已在组里的跳过）。

        :param group_id: 组 ID
        :param account_ids: 账号 ID 列表
        :raises GroupNotFound: 组不存在
        :returns: 实际新增的成员数
        """
        async with self._uow.begin() as session:
            if await session.get(AccountGroup, group_id) is None:
                raise GroupNotFound(f"组 {group_id} 不存在")
            existing = {
                int(value)
                for value in (
                    await session.execute(
                        select(account_group_members.c.account_id).where(
                            account_group_members.c.group_id == group_id
                        )
                    )
                ).scalars()
            }
            added = 0
            for account_id in account_ids:
                if account_id in existing:
                    continue
                # 只接受真实存在的账号（否则外键会在提交时报错，错误信息也不友好）
                if await session.get(Account, account_id) is None:
                    continue
                await session.execute(
                    account_group_members.insert().values(group_id=group_id, account_id=account_id)
                )
                added += 1
            await session.flush()
            return added

    async def remove_accounts(self, group_id: int, account_ids: Sequence[int]) -> int:
        """把账号移出组（**只解除关联，不删除账号**）。

        :param group_id: 组 ID
        :param account_ids: 账号 ID 列表
        :returns: 实际移除的成员数
        """
        async with self._uow.begin() as session:
            result = await session.execute(
                account_group_members.delete().where(
                    account_group_members.c.group_id == group_id,
                    account_group_members.c.account_id.in_(list(account_ids)),
                )
            )
            await session.flush()
            return int(getattr(result, "rowcount", 0) or 0)

    async def delete(self, group_id: int) -> bool:
        """删组（关联表由外键级联清理；**账号本身不受影响**）。"""
        async with self._uow.begin() as session:
            group = await session.get(AccountGroup, group_id)
            if group is None:
                return False
            await session.delete(group)
            await session.flush()
            return True
