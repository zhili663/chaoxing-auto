"""超星账号仓储。"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chaoxing.storage.models import Account


class AccountRepo:
    """账号的读写。

    :param session: 由 UnitOfWork 提供的会话
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(
        self, *, owner_user_id: int | None = None, include_disabled: bool = True
    ) -> Sequence[Account]:
        """按刷课顺序列出账号。

        :param owner_user_id: 只列某人的账号（``None`` = 不限，管理员视角）
        :param include_disabled: 是否包含已停用账号
        :returns: 账号列表（``priority`` 升序）
        """
        statement = select(Account)
        if owner_user_id is not None:
            statement = statement.where(Account.owner_user_id == owner_user_id)
        if not include_disabled:
            statement = statement.where(Account.enabled.is_(True))
        statement = statement.order_by(Account.priority, Account.id)
        return list((await self._session.scalars(statement)).all())

    async def get(self, account_id: int) -> Account | None:
        """按 ID 取账号。"""
        return await self._session.get(Account, account_id)

    async def get_by_username(self, username: str) -> Account | None:
        """按超星用户名取账号（导入去重用）。"""
        statement = select(Account).where(Account.username == username)
        return (await self._session.scalars(statement)).first()

    async def create(
        self,
        *,
        username: str,
        password_enc: str = "",
        owner_user_id: int | None = None,
        priority: int = 100,
        note: str = "",
    ) -> Account:
        """新建账号。

        :param username: 超星账号（手机号 / 用户名）
        :param password_enc: Fernet 加密后的密码
        :param owner_user_id: 属主（**可空**，管理员批量导入时不绑定）
        :returns: 新建的账号对象（已 flush，带 id）
        """
        account = Account(
            username=username,
            password_enc=password_enc,
            owner_user_id=owner_user_id,
            priority=priority,
            note=note,
        )
        self._session.add(account)
        await self._session.flush()
        return account

    async def update_state(
        self, account_id: int, *, status: str, session_state: dict[str, object] | None = None
    ) -> bool:
        """更新登录态（会话快照由 SessionRegistry 节流写入）。

        :returns: 账号是否存在并被更新
        """
        account = await self.get(account_id)
        if account is None:
            return False
        account.status = status
        if session_state is not None:
            account.session_state = dict(session_state)
        await self._session.flush()
        return True

    async def set_enabled(self, account_id: int, *, enabled: bool) -> bool:
        """启用 / 停用账号。"""
        account = await self.get(account_id)
        if account is None:
            return False
        account.enabled = enabled
        await self._session.flush()
        return True

    async def count(self) -> int:
        """账号总数。"""
        statement = select(Account.id)
        return len(list((await self._session.scalars(statement)).all()))
