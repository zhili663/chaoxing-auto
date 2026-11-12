"""全仓通用夹具：临时数据库 + 工作单元 + 基础数据。

放在 ``tests/`` 根上，让 storage 与 core 两层的用例共用同一套构造方式 ——
避免"每层各造一套夹具"导致行为不一致。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from chaoxing.config import get_settings, reload_settings
from chaoxing.storage.engine import create_all, create_engine, create_session_factory
from chaoxing.storage.models import Account
from chaoxing.storage.uow import UnitOfWork


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    """建在临时目录里的空库（跑完即弃，绝不碰 data/app.db）。"""
    engine = create_engine(tmp_path / "test.db")
    await create_all(engine)
    yield engine
    await engine.dispose()


@pytest.fixture
def uow(engine: AsyncEngine) -> UnitOfWork:
    """绑定到临时库的工作单元。"""
    return UnitOfWork(create_session_factory(engine))


@pytest.fixture
async def account_id(uow: UnitOfWork) -> int:
    """一个已存在的账号（课程 / 任务点都要挂在它下面）。"""
    async with uow.begin() as session:
        account = Account(username="13800000000")
        session.add(account)
        await session.flush()
        return account.id


@pytest.fixture
async def session(uow: UnitOfWork) -> AsyncIterator[AsyncSession]:
    """只读会话（断言用）。"""
    async with uow.read() as session:
        yield session


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """把 CLI 指向临时目录（``cx`` 命令的测试共用）。

    关键点：``get_settings()`` 是带缓存的单例，光给 ``CliRunner`` 传 ``env=``
    不会生效（第一次加载的配置会一直用下去，测试之间互相污染）。
    所以这里设环境变量后**显式清一次缓存**。
    """
    monkeypatch.setenv("APP__DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SECRET_KEY", "A" * 43 + "=")
    monkeypatch.setenv("APP__HOST", "127.0.0.1")
    reload_settings()

    settings = get_settings()
    assert settings.db_path.parent == tmp_path, "配置没指向临时目录，测试会污染真实库"

    async def prepare() -> None:
        engine = create_engine(settings.db_path)
        try:
            await create_all(engine)
        finally:
            await engine.dispose()

    asyncio.run(prepare())
