"""工作单元：写串行、异常回滚、读不阻塞。

来源：SQLite 写-写互斥（并发写会撞 "database is locked"）+ "半条数据"事故
（提交到一半崩溃，列表出现幽灵记录）。
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chaoxing.storage.models import User
from chaoxing.storage.uow import UnitOfWork


async def _count_users(session: AsyncSession) -> int:
    """临时库里的用户数（回滚断言要用）。"""
    return int((await session.scalar(select(func.count()).select_from(User))) or 0)


async def test_write_transactions_are_serialized(uow: UnitOfWork) -> None:
    """两个写事务不得交叉执行（SQLite 写-写的进程内保证）。"""
    order: list[str] = []

    async def worker(tag: str) -> None:
        async with uow.begin():
            order.append(f"{tag}-in")
            await asyncio.sleep(0.01)
            order.append(f"{tag}-out")

    await asyncio.gather(worker("a"), worker("b"))

    assert order in (
        ["a-in", "a-out", "b-in", "b-out"],
        ["b-in", "b-out", "a-in", "a-out"],
    )


async def test_commit_persists_rows(uow: UnitOfWork) -> None:
    """正常退出即提交。"""
    async with uow.begin() as session:
        session.add(User(username="alice"))

    async with uow.read() as session:
        assert await _count_users(session) == 1


async def test_exception_rolls_back_everything(uow: UnitOfWork) -> None:
    """中途抛异常时不留半条数据（调用方不必自己 rollback）。"""
    with pytest.raises(RuntimeError, match="boom"):
        async with uow.begin() as session:
            session.add(User(username="ghost"))
            await session.flush()
            raise RuntimeError("boom")

    async with uow.read() as session:
        assert await _count_users(session) == 0


async def test_read_does_not_need_write_lock(uow: UnitOfWork) -> None:
    """只读会话不被写锁挡住（WAL 下读写不互斥）。"""
    async with uow.begin() as session:
        session.add(User(username="bob"))

    results: list[int] = []

    async def reader() -> None:
        async with uow.read() as session:
            results.append(await _count_users(session))

    async with uow.begin():
        await asyncio.gather(reader())
        results.append(-1)  # 写事务内部的标记

    assert 1 in results


async def test_unique_constraint_is_enforced(uow: UnitOfWork) -> None:
    """用户名的唯一约束必须真的起作用（否则并发注册会造出重复账号）。"""
    from sqlalchemy.exc import IntegrityError

    async with uow.begin() as session:
        session.add(User(username="dup"))

    with pytest.raises(IntegrityError):
        async with uow.begin() as session:
            session.add(User(username="dup"))


async def test_query_after_commit_still_readable(uow: UnitOfWork) -> None:
    """``expire_on_commit=False``：提交后仍能读对象属性（避免额外往返）。"""
    async with uow.begin() as session:
        user = User(username="carol")
        session.add(user)

    assert user.username == "carol"

    async with uow.read() as session:
        assert (await session.scalars(select(User).where(User.username == "carol"))).first()
