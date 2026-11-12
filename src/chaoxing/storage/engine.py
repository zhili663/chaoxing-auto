"""数据库引擎与会话工厂（SQLite + aiosqlite + WAL）。

为什么在 ``connect`` 事件里设 PRAGMA
------------------------------------
SQLite 的这几个开关是**连接级**的，不是建库时一次性写入的：每新建一条连接都要重设，
否则某些连接跑在 rollback journal 模式下，并发写会直接 "database is locked"。

四个 PRAGMA 的作用：

* ``journal_mode=WAL`` —— 读写不互斥（后台刷课写库时，网页查询不会被阻塞）；
* ``synchronous=NORMAL`` —— WAL 下的安全/性能平衡点；
* ``busy_timeout=5000`` —— 写锁竞争时等待而不是立刻报错；
* ``foreign_keys=ON`` —— SQLite 默认**不**强制外键，必须显式打开。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from chaoxing.storage.base import Base

log = logging.getLogger(__name__)

#: 建库时下发的 PRAGMA（连接级，每次连接都要重设）
PRAGMAS: tuple[str, ...] = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA busy_timeout=5000",
    "PRAGMA foreign_keys=ON",
)


def database_url(db_path: Path) -> str:
    """把本地路径转成 SQLAlchemy 的 async SQLite URL。

    :param db_path: SQLite 文件路径
    :returns: 形如 ``sqlite+aiosqlite:///D:/path/app.db``
    """
    return f"sqlite+aiosqlite:///{db_path.as_posix()}"


def create_engine(db_path: Path, *, echo: bool = False) -> AsyncEngine:
    """创建 async 引擎并挂上 PRAGMA 初始化。

    :param db_path: SQLite 文件路径（父目录会被自动创建）
    :param echo: 是否打印 SQL（排障用）
    :returns: :class:`~sqlalchemy.ext.asyncio.AsyncEngine`
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(database_url(db_path), echo=echo, future=True)

    @event.listens_for(engine.sync_engine, "connect")
    def _apply_pragmas(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            for statement in PRAGMAS:
                cursor.execute(statement)
        finally:
            cursor.close()

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """创建会话工厂（``expire_on_commit=False``：提交后仍可读对象属性）。

    :param engine: async 引擎
    :returns: :class:`~sqlalchemy.ext.asyncio.async_sessionmaker`
    """
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def create_all(engine: AsyncEngine) -> None:
    """按当前模型建表（**测试与 `cx db upgrade` 之外的场景不要用**）。

    生产环境的 schema 演进走 Alembic；这里只为测试与首次自检提供一条捷径。
    """
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def drop_all(engine: AsyncEngine) -> None:
    """删掉全部表（仅测试用）。"""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)


async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """一个最简单的会话上下文（仓储之外的散用场景）。

    :param factory: 会话工厂
    :yields: :class:`AsyncSession`
    """
    async with factory() as session:
        yield session
