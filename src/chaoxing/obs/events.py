"""领域事件与进程内事件总线。

事件是解耦的唯一钩子（``docs/REFACTOR_PLAN.md`` §5.3）：刷课编排只负责"发事件"，
谁在听（SSE 广播、审计落库、统计聚合）它一概不知。

**背压策略**：每个订阅者一条**有界队列**，满了丢最旧的并计数。
理由 —— 发布者跑在刷课关键路径上（一次心跳的延迟直接影响时间轴），
一个慢订阅者（比如往磁盘写日志）绝不能把发布者拖住；丢几条事件远好过卡住刷课。

依赖约束：本模块属于 ``obs`` 层（最底层），**只吃事件值对象**，
不得引用 storage / core / client / api / cli。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

log = logging.getLogger(__name__)


def now() -> datetime:
    """事件发生时间（UTC）。"""
    return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# 事件定义（frozen dataclass：事件一旦发出就不该被修改）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Event:
    """所有领域事件的基类。"""

    occurred_at: datetime = field(default_factory=now, kw_only=True)


@dataclass(frozen=True, slots=True)
class AccountLoggedIn(Event):
    """账号登录成功。"""

    account_id: int = 0
    username: str = ""


@dataclass(frozen=True, slots=True)
class SessionExpired(Event):
    """登录态失效。"""

    account_id: int = 0
    reason: str = ""


@dataclass(frozen=True, slots=True)
class HumanRequired(Event):
    """需要人工介入（验证码 / 滑块 / 人脸）。"""

    account_id: int = 0
    reason: str = "captcha"
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CourseScanned(Event):
    """课程扫描完成。"""

    account_id: int = 0
    course_key: str = ""
    point_count: int = 0


@dataclass(frozen=True, slots=True)
class TaskPointStarted(Event):
    """开始处理一个任务点。"""

    account_id: int = 0
    course_id: int = 0
    point_id: str = ""
    kind: str = ""


@dataclass(frozen=True, slots=True)
class TaskPointProgress(Event):
    """任务点进度推进（视频心跳）。"""

    account_id: int = 0
    point_id: str = ""
    progress: float = 0.0
    duration: float = 0.0


@dataclass(frozen=True, slots=True)
class TaskPointFinished(Event):
    """任务点结束（无论成功与否）。"""

    account_id: int = 0
    course_id: int = 0
    point_id: str = ""
    kind: str = ""
    status: str = "done"
    detail: str = ""
    elapsed_s: float = 0.0


@dataclass(frozen=True, slots=True)
class QuizAnswered(Event):
    """一次测验作答（刷题记录通道）。"""

    account_id: int = 0
    work_id: str = ""
    question_count: int = 0
    answered: int = 0
    submitted: bool = False
    confidence: float = 0.0


@dataclass(frozen=True, slots=True)
class StudyRunFinished(Event):
    """一轮刷课结束。"""

    account_id: int = 0
    done: int = 0
    failed: int = 0
    blocked: int = 0


EventHandler = Callable[[Event], Awaitable[None]]

_STOP = object()


class _Subscription:
    """一个订阅：有界队列 + 消费任务 + 丢弃计数。"""

    def __init__(self, handler: EventHandler, *, maxsize: int) -> None:
        self.handler = handler
        self.maxsize = maxsize
        self.queue: asyncio.Queue[object] = asyncio.Queue(maxsize=maxsize)
        self.task: asyncio.Task[None] | None = None
        self.dropped = 0
        self.handled = 0
        self.errors = 0

    def offer(self, event: Event) -> None:
        """非阻塞投递：队列满时丢**最旧**的一条（保证最新状态总能到达）。"""
        try:
            self.queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            pass
        with contextlib.suppress(asyncio.QueueEmpty):
            self.queue.get_nowait()  # 丢最旧
        self.dropped += 1
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:  # pragma: no cover - 极端竞态
            self.dropped += 1


class EventBus:
    """进程内事件总线（单进程部署是既定约束，见 REFACTOR_PLAN §10）。

    :param default_maxsize: 订阅者队列的默认容量
    """

    def __init__(self, *, default_maxsize: int = 256) -> None:
        self._default_maxsize = default_maxsize
        self._subscriptions: list[_Subscription] = []
        self._closed = False
        self.published = 0

    # ------------------------------------------------------------------ #
    # 订阅
    # ------------------------------------------------------------------ #
    def subscribe(self, handler: EventHandler, *, maxsize: int | None = None) -> _Subscription:
        """注册一个订阅者并启动其消费任务。

        **必须在运行中的事件循环里调用**（内部用 ``create_task``）。

        :param handler: 异步处理函数
        :param maxsize: 该订阅者的队列容量
        :returns: 订阅句柄（可查 ``dropped`` / ``handled``）
        """
        subscription = _Subscription(handler, maxsize=maxsize or self._default_maxsize)
        subscription.task = asyncio.create_task(self._pump(subscription))
        self._subscriptions.append(subscription)
        return subscription

    async def _pump(self, subscription: _Subscription) -> None:
        """消费循环：异常只计数，绝不让总线倒掉。"""
        while True:
            item = await subscription.queue.get()
            if item is _STOP:
                subscription.queue.task_done()
                return
            try:
                await subscription.handler(item)  # type: ignore[arg-type]
                subscription.handled += 1
            except Exception:
                subscription.errors += 1
                log.exception("事件订阅者处理失败")
            finally:
                subscription.queue.task_done()

    # ------------------------------------------------------------------ #
    # 发布
    # ------------------------------------------------------------------ #
    def publish(self, event: Event) -> None:
        """发布事件（**同步、非阻塞**：入队即返回）。"""
        if self._closed:
            return
        self.published += 1
        for subscription in self._subscriptions:
            subscription.offer(event)

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def unsubscribe(self, subscription: _Subscription) -> None:
        """取消一个订阅（SSE 客户端断连时调用）。

        不取消的话，慢客户端的队列会一直被投递然后丢旧 —— 白白消耗 CPU 且掩盖问题。

        :param subscription: :meth:`subscribe` 返回的句柄
        """
        with contextlib.suppress(ValueError):
            self._subscriptions.remove(subscription)
        if subscription.task is not None and not subscription.task.done():
            subscription.task.cancel()

    async def wait_idle(self, *, timeout: float = 2.0) -> bool:
        """等所有订阅者把手头的活干完（测试与优雅退出用）。

        :param timeout: 最长等待秒数
        :returns: 是否在超时前清空
        """
        try:
            async with asyncio.timeout(timeout):
                await asyncio.gather(*(sub.queue.join() for sub in self._subscriptions))
        except TimeoutError:
            return False
        return True

    async def close(self) -> None:
        """停止全部订阅任务。"""
        self._closed = True
        for subscription in self._subscriptions:
            if subscription.task is not None:
                subscription.task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await subscription.task
        self._subscriptions.clear()

    # ------------------------------------------------------------------ #
    # 观测
    # ------------------------------------------------------------------ #
    def stats(self) -> dict[str, int]:
        """总线与订阅者的运行统计（``/system`` 与体检用）。"""
        return {
            "published": self.published,
            "subscribers": len(self._subscriptions),
            "dropped": sum(sub.dropped for sub in self._subscriptions),
            "handled": sum(sub.handled for sub in self._subscriptions),
            "errors": sum(sub.errors for sub in self._subscriptions),
        }
