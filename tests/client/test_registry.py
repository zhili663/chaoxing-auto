"""会话注册表：账号级互斥、节流持久化、恢复。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

from chaoxing.client import SessionRegistry, SessionState


class _FakeStore:
    """内存版 SessionStore（记录写入次数，便于断言"节流"是否生效）。"""

    def __init__(self) -> None:
        self.items: dict[str, Mapping[str, Any]] = {}
        self.writes = 0

    async def save(self, snapshot: Mapping[str, Any]) -> None:
        await asyncio.sleep(0)  # 模拟真实 I/O，暴露并发问题
        self.items[str(snapshot["account_id"])] = snapshot
        self.writes += 1

    async def load_all(self) -> Sequence[Mapping[str, Any]]:
        return list(self.items.values())


async def test_acquire_serializes_same_account() -> None:
    """同一账号的并发 acquire 必须排队（否则同一账号的时间轴会被打乱）。"""
    registry = SessionRegistry()
    order: list[str] = []

    async def worker(tag: str) -> None:
        async with registry.acquire(1):
            order.append(f"{tag}-in")
            await asyncio.sleep(0.01)
            order.append(f"{tag}-out")

    await asyncio.gather(worker("a"), worker("b"))

    assert order in (["a-in", "a-out", "b-in", "b-out"], ["b-in", "b-out", "a-in", "a-out"])


async def test_different_accounts_run_in_parallel() -> None:
    """不同账号之间不应互斥（否则多账号并发就没了）。"""
    registry = SessionRegistry()
    started = asyncio.Event()

    async def hold(account_id: int) -> None:
        async with registry.acquire(account_id):
            started.set()
            await asyncio.wait_for(started.wait(), timeout=1)

    await asyncio.gather(hold(1), hold(2))


async def test_get_returns_same_object_and_records_username() -> None:
    """get 必须幂等返回同一实例（cookie 不能分裂成两份）。"""
    registry = SessionRegistry()

    first = registry.get(1, username="13800000000")
    second = registry.get(1)

    assert first is second
    assert second.username == "13800000000"


async def test_persist_writes_only_when_dirty() -> None:
    """节流：cookie 几乎每响应都变，只在确有变化时落库。"""
    store = _FakeStore()
    registry = SessionRegistry(store=store)
    session = registry.get(1)

    assert await registry.persist(1) is False

    session.cookies["_uid"] = "42"
    session.dirty = True

    assert await registry.persist(1) is True
    assert await registry.persist(1) is False  # 第二次不再写
    assert store.writes == 1


async def test_restore_loads_snapshots_into_registry() -> None:
    """启动恢复：快照里的 cookie 要原样回到注册表。"""
    store = _FakeStore()
    await store.save(
        SessionState(
            account_id=3, username="u3", cookies={"_uid": "9"}, status="logged_in"
        ).snapshot()
    )
    registry = SessionRegistry(store=store)

    restored = await registry.restore()

    assert restored == 1
    assert registry.get(3).cookies == {"_uid": "9"}
    assert registry.get(3).is_authenticated


async def test_invalidate_marks_session_expired() -> None:
    """登录态失效时要把状态标出来（前端账户栏靠它显示红灯）。"""
    registry = SessionRegistry()
    registry.get(1).status = "logged_in"

    registry.invalidate(1)

    assert registry.get(1).status == "expired"
    assert registry.get(1).dirty is True


async def test_remove_drops_session_and_lock() -> None:
    """登出后会话与锁都要清掉（否则重新登录会撞上一把陈旧的锁）。"""
    registry = SessionRegistry()
    registry.get(1)

    registry.remove(1)

    assert registry.has(1) is False
    assert registry.all() == []
