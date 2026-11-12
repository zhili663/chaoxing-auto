"""会话持久化：把 ``SessionRegistry`` 的快照存进 ``accounts`` 表。

位置说明：本模块只做"存取"，协调 ``UnitOfWork`` 与 ``SecretBox``，
因此属于 storage 层（分层契约允许 storage 依赖 utils）。
``client`` 侧的 :class:`~chaoxing.client.registry.SessionStore` 是它实现的协议 ——
client 不反过来依赖 storage。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select

from chaoxing.storage.base import utcnow
from chaoxing.storage.models import Account
from chaoxing.storage.uow import UnitOfWork
from chaoxing.utils.crypto import SecretBox

log = logging.getLogger(__name__)


class DatabaseSessionStore:
    """会话快照的库内存储（cookie 加密后写入 ``accounts.cookie_enc``）。

    :param uow: 工作单元
    :param box: 凭据加解密
    """

    def __init__(self, uow: UnitOfWork, box: SecretBox) -> None:
        self._uow = uow
        self._box = box

    async def save(self, snapshot: Mapping[str, Any]) -> None:
        """写入一条会话快照。

        :param snapshot: :meth:`SessionState.snapshot` 的产物
        """
        account_id = int(snapshot.get("account_id", 0) or 0)
        if not account_id:
            return
        async with self._uow.begin() as session:
            account = await session.get(Account, account_id)
            if account is None:
                return
            account.cookie_enc = self._box.encrypt_json(dict(snapshot))
            account.status = str(snapshot.get("status", account.status))
            account.last_check_at = utcnow()
            await session.flush()

    async def load_all(self) -> Sequence[Mapping[str, Any]]:
        """读出全部可用会话快照（装进 ``SessionRegistry`` 供启动恢复）。"""
        async with self._uow.read() as session:
            rows = list(
                (
                    await session.scalars(
                        select(Account).where(Account.cookie_enc != "").order_by(Account.id)
                    )
                ).all()
            )

        snapshots: list[Mapping[str, Any]] = []
        for account in rows:
            payload = self._box.decrypt_json(account.cookie_enc)
            if not payload:
                continue
            payload.setdefault("account_id", account.id)
            payload.setdefault("username", account.username)
            snapshots.append(payload)
        return snapshots
