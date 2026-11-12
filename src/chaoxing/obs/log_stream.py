"""app 日志的实时广播（``GET /api/stream/logs`` 的数据源）。

为什么另起一套而不是复用 :class:`chaoxing.obs.events.EventBus`：那里的事件是**异步**产生的
（发布者本身就在事件循环里，可以 ``await`` 消费者），而日志是**同步**产生的 —— structlog
处理器跑在 ``log.info()`` 的调用栈上，可能在 CLI、测试或任意工作线程里，那里既没有事件
循环、也不该等消费者。所以这里同步把记录塞进订阅者的有界队列，消费端（SSE 生成器）自己取：

* **写日志的路径永不被拖慢**：队列满了丢最旧的一条并计数（与 ``EventBus`` 同款策略）；
* **回放尾部**：广播器保留最近 :data:`HISTORY_LIMIT` 条，新连接可按 ``backlog`` 先补历史；
* **级别过滤在订阅侧**：``min_level`` 的语义与 ``GET /api/logs`` 完全一致
  （复用 :data:`chaoxing.obs.log_reader.LEVELS`）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from chaoxing.obs.log_reader import LEVELS

#: 回放缓冲长度（新连接最多能补多少条历史；也是 ``backlog`` 参数的上限）
HISTORY_LIMIT = 200
#: 单个订阅者的默认队列容量（满了丢最旧）
DEFAULT_MAXSIZE = 128
#: ``event_dict`` 缺字段时的兜底级别（正常链路上由 ``add_log_level`` 写入）
DEFAULT_LEVEL = "info"


@dataclass(frozen=True, slots=True)
class LogRecord:
    """面板要的四字段（与 ``GET /api/logs`` 的 JSON 行、SSE ``log`` 事件一致）。"""

    level: str
    logger: str
    event: str
    timestamp: str

    def as_dict(self) -> dict[str, str]:
        """转成可直接 ``json.dumps`` 的载荷。"""
        return {
            "level": self.level,
            "logger": self.logger,
            "event": self.event,
            "timestamp": self.timestamp,
        }


def record_of(event_dict: Mapping[str, Any]) -> LogRecord:
    """从 structlog 的 ``event_dict`` 里取面板要的四个字段。

    ``level`` / ``logger`` / ``timestamp`` 由前置处理器写入，因此广播处理器必须排在它们
    **之后**（顺序见 :func:`chaoxing.obs.logging._build_processors`）。
    """
    return LogRecord(
        level=str(event_dict.get("level", DEFAULT_LEVEL)).lower(),
        logger=str(event_dict.get("logger", "")),
        event=str(event_dict.get("event", "")),
        timestamp=str(event_dict.get("timestamp", "")),
    )


class LogSubscription:
    """一个订阅者：有界队列 + 丢弃计数。

    投递是**同步**的（写日志的一方说了算）：队列满了丢最旧的一条并记数，绝不等消费端。
    消费端用 :meth:`wait` 取成批的记录，超时即返回空表（SSE 用它发保活注释）。
    """

    #: 队列容量（回放条数更大时会放大，否则回放会被自己的丢弃策略吃掉）
    maxsize: int
    #: 订阅时声明的级别门槛（回显用）
    min_level: str
    #: 因队列满被丢掉的条数
    dropped: int
    #: 已经被消费端取走的条数
    delivered: int

    def __init__(self, *, maxsize: int, min_level: str) -> None:
        self.maxsize = maxsize
        self.min_level = min_level
        self._allowed = LEVELS.get(min_level.upper(), LEVELS["INFO"])
        self._queue: deque[LogRecord] = deque()
        self._lock = threading.Lock()
        #: 只在消费端第一次 ``wait`` 时绑定（那时一定有事件循环与线程上下文）
        self._ready: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False
        self.dropped = 0
        self.delivered = 0

    # ------------------------------------------------------------------ #
    # 消费端
    # ------------------------------------------------------------------ #
    def accepts(self, record: LogRecord) -> bool:
        """这条记录是否达到订阅者的级别门槛。"""
        return record.level in self._allowed

    def drain(self) -> list[LogRecord]:
        """取走当前所有记录（不阻塞）。"""
        with self._lock:
            batch = list(self._queue)
            self._queue.clear()
        self.delivered += len(batch)
        return batch

    async def wait(self, timeout: float) -> list[LogRecord]:
        """等一批记录（超时返回空表，让调用方去发保活）。

        :param timeout: 最长等待秒数
        :returns: 记录列表（可能为空）
        """
        if self._ready is None:
            # 先绑定事件再取队列：这之后 offer 一定能叫醒等待方（没有丢唤醒的窗口）
            self._loop = asyncio.get_running_loop()
            self._ready = asyncio.Event()
        batch = self.drain()
        if batch:
            return batch
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._ready.wait(), timeout)
        self._ready.clear()
        return self.drain()

    # ------------------------------------------------------------------ #
    # 发布端（同步，可能在别的线程）
    # ------------------------------------------------------------------ #
    def offer(self, record: LogRecord) -> None:
        """非阻塞投递：队列满了丢**最旧**的一条（保证最新日志总能到达）。"""
        if not self.accepts(record):
            return
        with self._lock:
            if self._closed:
                return
            if len(self._queue) >= self.maxsize:
                self._queue.popleft()
                self.dropped += 1
            self._queue.append(record)
        self._wake()

    def seed(self, records: Iterable[LogRecord]) -> None:
        """回放缓冲里的尾部（只在订阅时装一次）。"""
        with self._lock:
            self._queue.extend(records)

    def close(self) -> None:
        """退订后收尾（清空剩余记录，避免断连的连接继续被投递）。"""
        with self._lock:
            self._closed = True
            self._queue.clear()

    def _wake(self) -> None:
        """叫醒等待中的消费端（跨线程安全；没有消费端时什么都不做）。"""
        loop, ready = self._loop, self._ready
        if loop is None or ready is None or loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):  # 循环正好在关闭竞态里先走一步
            loop.call_soon_threadsafe(ready.set)


class LogBroadcaster:
    """进程内日志广播（单进程部署是既定约束，见 ``docs/REFACTOR_PLAN.md`` §10）。

    :param history: 回放缓冲长度（默认 :data:`HISTORY_LIMIT`）
    """

    def __init__(self, *, history: int = HISTORY_LIMIT) -> None:
        self.history_limit = history
        self._history: deque[LogRecord] = deque(maxlen=history)
        self._lock = threading.Lock()
        self._subscriptions: list[LogSubscription] = []
        #: 累计收到的日志条数
        self.published = 0

    # ------------------------------------------------------------------ #
    # 订阅
    # ------------------------------------------------------------------ #
    def subscribe(
        self, *, maxsize: int | None = None, min_level: str = "info", backlog: int = 0
    ) -> LogSubscription:
        """注册一个订阅者（同步，任何线程、任何上下文都能调用）。

        :param maxsize: 实时队列容量（默认 :data:`DEFAULT_MAXSIZE`）
        :param min_level: 只收这个级别及以上（语义同 ``GET /api/logs``）
        :param backlog: 先把缓冲里最近的多少条补进队列（0 = 只推增量）
        :returns: 订阅句柄
        """
        wanted = max(0, min(backlog, self.history_limit))
        subscription = LogSubscription(
            maxsize=max(maxsize or DEFAULT_MAXSIZE, wanted), min_level=min_level
        )
        with self._lock:
            if wanted:
                tail = [item for item in self._history if subscription.accepts(item)][-wanted:]
                subscription.seed(tail)
            self._subscriptions.append(subscription)
        return subscription

    def unsubscribe(self, subscription: LogSubscription) -> None:
        """取消订阅（SSE 客户端断连时调用；重复调用无害）。"""
        with self._lock, contextlib.suppress(ValueError):
            self._subscriptions.remove(subscription)
        subscription.close()

    # ------------------------------------------------------------------ #
    # 发布
    # ------------------------------------------------------------------ #
    def publish(self, record: LogRecord) -> None:
        """同步、非阻塞地投给所有订阅者（跑在写日志的路径上，必须便宜）。"""
        with self._lock:
            self.published += 1
            self._history.append(record)
            subscriptions = list(self._subscriptions)
        for subscription in subscriptions:
            subscription.offer(record)

    def recent(self, count: int) -> list[LogRecord]:
        """缓冲里最近的 ``count`` 条（不能订阅、只想看一眼尾部时用）。"""
        with self._lock:
            return list(self._history)[-max(0, min(count, self.history_limit)) :]

    def stats(self) -> dict[str, int]:
        """观测快照（订阅者数量 / 累计条数 / 丢弃数 / 缓冲占用）。"""
        with self._lock:
            subscriptions = list(self._subscriptions)
        return {
            "subscribers": len(subscriptions),
            "published": self.published,
            "dropped": sum(item.dropped for item in subscriptions),
            "buffered": len(self._history),
        }


#: 进程内单例（structlog 处理器与 SSE 路由共用同一个实例）
log_broadcaster = LogBroadcaster()


def record_of_log_record(record: logging.LogRecord) -> LogRecord:
    """从 stdlib 的 ``LogRecord`` 取面板要的四个字段。

    structlog 原生调用经 ``wrap_for_formatter`` 之后，``record.msg`` 就是那份 ``event_dict``
    （渲染交给 formatter），所以两种形态都要认：dict 走 :func:`record_of`，字符串按 stdlib 取。
    """
    if isinstance(record.msg, Mapping):
        return record_of(record.msg)
    stamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat().replace("+00:00", "Z")
    return LogRecord(
        level=record.levelname.lower(),
        logger=record.name,
        event=record.getMessage(),
        timestamp=stamp,
    )


class LogStreamHandler(logging.Handler):
    """把每条 ``LogRecord`` 推给 SSE 广播器（**每个 record 恰好一次**）。

    为什么是 Handler 而不是 processor（2026-10-11 实测的缺陷）：``ProcessorFormatter``
    会给**每个 handler 各跑一遍**前置链（本仓库是控制台 + 文件两个），而且每次 ``format()``
    还会**复制** record —— 所以"按 record 去重"在处理器里根本做不到，一次 ``log.info``
    会在面板里出现两行。Handler 的 ``emit`` 对每个 record 只调一次，且不必 ``format()``。
    """

    def emit(self, record: logging.LogRecord) -> None:
        """投递一条记录；广播自身出问题绝不影响业务日志。

        :param record: stdlib 日志记录
        """
        try:
            log_broadcaster.publish(record_of_log_record(record))
        except Exception:
            self.handleError(record)


__all__ = [
    "DEFAULT_MAXSIZE",
    "HISTORY_LIMIT",
    "LogBroadcaster",
    "LogRecord",
    "LogStreamHandler",
    "LogSubscription",
    "log_broadcaster",
    "record_of",
    "record_of_log_record",
]
