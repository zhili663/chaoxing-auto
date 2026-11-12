"""工作单元（UnitOfWork）：事务边界与**单写者**约束。

为什么必须串行写
----------------
SQLite 的写事务是数据库级串行的（WAL 让读写不互斥，但写与写互斥）。业务侧有多个
并发写入源（账号 worker 落进度、审计写入、统计增量），放任并发就会在
``busy_timeout`` 之后开始报 ``database is locked``。

这里用一把进程内互斥锁把**写事务**串起来：代价是写变慢，收益是永不锁库。
单进程部署是既定约束（见 ``docs/REFACTOR_PLAN.md`` §10 风险 4）。

读写分离：``read()`` 不加锁（WAL 下读不会被写阻塞），``begin()`` 拿锁。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = logging.getLogger(__name__)


class UnitOfWork:
    """事务与写锁的统一入口。

    :param factory: async 会话工厂
    :param write_lock: 进程内写锁（多实例共享同一把；默认自建）
    """

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        write_lock: asyncio.Lock | None = None,
    ) -> None:
        self._factory = factory
        self._write_lock = write_lock or asyncio.Lock()

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[AsyncSession]:
        """开启一个**写事务**（串行执行）。

        正常退出即提交，异常则回滚 —— 调用方不需要自己 commit/rollback。

        :yields: :class:`AsyncSession`
        """
        async with self._write_lock, self._factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            await session.commit()

    @asynccontextmanager
    async def read(self) -> AsyncIterator[AsyncSession]:
        """开启一个只读会话（不加写锁，WAL 下不被写阻塞）。

        :yields: :class:`AsyncSession`
        """
        async with self._factory() as session:
            yield session
