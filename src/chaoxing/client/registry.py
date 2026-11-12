"""运行时会话注册表（Chaoxing User Registry）。

即使不用 httpx 的 cookie jar，也必须有一个**规范化的注册表**管住超星账号的会话状态，
否则 cookie 会散落到各处被随手改 —— 然后某天有人漏了 Referer，403 满天飞。

职责边界（硬性）
----------------
* :class:`~chaoxing.client.session.SessionState` 只从本注册表取，**不要在别处新建**；
* **账号级互斥**：同一账号同时只允许一个刷课单元运行（``acquire()`` 即锁）；
* 状态变更留 hook，P3 接事件总线后广播给 SSE（当前只记日志）。

依赖方向：本模块属于 ``client`` 层，**不得依赖 storage**；持久化通过注入的
:class:`SessionStore` 协议完成（P2 由 storage 实现，当前由 CLI 写本地 JSON 文件）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any, Protocol

from chaoxing.client.session import SessionState

log = logging.getLogger(__name__)


class SessionStore(Protocol):
    """会话持久化协议（由上层注入实现）。"""

    async def save(self, snapshot: Mapping[str, Any]) -> None:
        """保存一份会话快照。"""
        ...

    async def load_all(self) -> Sequence[Mapping[str, Any]]:
        """载入全部会话快照。"""
        ...


class SessionRegistry:
    """账号会话的唯一真相源。

    :param store: 可选的持久化后端；为空则纯内存（测试与临时 CLI 用）
    """

    def __init__(self, *, store: SessionStore | None = None) -> None:
        self._sessions: dict[int, SessionState] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._store = store

    # ------------------------------------------------------------------ #
    # 读写
    # ------------------------------------------------------------------ #
    def get(self, account_id: int, *, username: str = "") -> SessionState:
        """取（必要时创建）账号会话。

        :param account_id: 账号 ID
        :param username: 首次创建时记录用户名（仅用于展示）
        :returns: 该账号的会话状态
        """
        state = self._sessions.get(account_id)
        if state is None:
            state = SessionState(account_id=account_id, username=username)
            self._sessions[account_id] = state
        elif username and not state.username:
            state.username = username
        return state

    def has(self, account_id: int) -> bool:
        """该账号是否已有会话对象。"""
        return account_id in self._sessions

    def put(self, state: SessionState) -> None:
        """直接放入一个已知状态的会话（恢复 / 测试用）。"""
        self._sessions[state.account_id] = state

    def remove(self, account_id: int) -> None:
        """移除会话（登出）。"""
        self._sessions.pop(account_id, None)
        self._locks.pop(account_id, None)

    def all(self) -> list[SessionState]:
        """全部会话（按账号 ID 排序，输出稳定）。"""
        return [self._sessions[key] for key in sorted(self._sessions)]

    def snapshot(self) -> list[dict[str, Any]]:
        """全部会话的可序列化快照。"""
        return [state.snapshot() for state in self.all()]

    # ------------------------------------------------------------------ #
    # 互斥
    # ------------------------------------------------------------------ #
    @asynccontextmanager
    async def acquire(self, account_id: int) -> AsyncIterator[SessionState]:
        """独占某账号的会话（同账号并发调用会排队）。

        典型场景：调度器给每个账号起一个 worker，worker 内的所有请求都应当
        在这个上下文里发，避免同一账号的时间轴被并发请求打乱。

        :param account_id: 账号 ID
        :yields: 该账号的 :class:`SessionState`
        """
        lock = self._locks.setdefault(account_id, asyncio.Lock())
        async with lock:
            yield self.get(account_id)

    # ------------------------------------------------------------------ #
    # 状态
    # ------------------------------------------------------------------ #
    def invalidate(self, account_id: int) -> None:
        """把会话标记为过期（登录态失效时调用）。"""
        state = self._sessions.get(account_id)
        if state is not None:
            state.status = "expired"
            state.dirty = True
            log.info("会话已标记过期 account_id=%s", account_id)

    # ------------------------------------------------------------------ #
    # 持久化（节流：只在确有变化时写）
    # ------------------------------------------------------------------ #
    async def persist(self, account_id: int) -> bool:
        """把指定会话落库（仅在 ``dirty`` 时执行）。

        :param account_id: 账号 ID
        :returns: 是否真的写了一次
        """
        state = self._sessions.get(account_id)
        if state is None or not state.dirty or self._store is None:
            return False
        await self._store.save(state.snapshot())
        state.dirty = False
        return True

    async def restore(self) -> int:
        """从存储恢复全部会话。

        :returns: 恢复的会话条数
        """
        if self._store is None:
            return 0
        snapshots = await self._store.load_all()
        for raw in snapshots:
            state = SessionState.from_snapshot(dict(raw))
            self._sessions[state.account_id] = state
        if snapshots:
            log.info("已恢复 %d 个会话", len(snapshots))
        return len(snapshots)
