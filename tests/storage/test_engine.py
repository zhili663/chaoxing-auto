"""引擎层的连接级设置：WAL 与外键。

来源：legacy 的真实故障 —— SQLite 默认**不强制外键**，且 PRAGMA 是连接级而非库级，
漏设的连接会跑在 rollback journal 模式，并发写直接 "database is locked"。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from chaoxing.storage.uow import UnitOfWork


async def test_wal_and_foreign_keys_are_enabled(engine: AsyncEngine) -> None:
    """每条连接都要看到 WAL 与外键开关（否则读写会互相阻塞 / 约束形同虚设）。"""
    async with engine.connect() as connection:
        journal = (await connection.exec_driver_sql("PRAGMA journal_mode")).scalar()
        foreign_keys = (await connection.exec_driver_sql("PRAGMA foreign_keys")).scalar()
        busy_timeout = (await connection.exec_driver_sql("PRAGMA busy_timeout")).scalar()

    assert str(journal).lower() == "wal"
    assert int(foreign_keys) == 1
    assert int(busy_timeout) == 5000


async def test_pragmas_apply_to_every_new_connection(engine: AsyncEngine) -> None:
    """换一条连接同样成立 —— 这正说明"连接级设置"没被漏掉。"""
    for _ in range(3):
        async with engine.connect() as connection:
            assert int(await connection.scalar(text("PRAGMA foreign_keys"))) == 1


async def test_foreign_key_violation_is_rejected(engine: AsyncEngine, uow: UnitOfWork) -> None:
    """外键打开后，引用不存在的账号必须报错（而不是静默写入脏数据）。"""
    from chaoxing.storage.models import Course

    with pytest.raises(IntegrityError):
        async with uow.begin() as session:
            session.add(Course(account_id=999_999, course_key="1:2", title="幽灵课程"))
