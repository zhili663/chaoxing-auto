"""事件总线：投递、背压、订阅者隔离。

背压这条是重点：发布者跑在**刷课关键路径**上（一次心跳的延迟直接影响播放时间轴），
所以总线必须是同步非阻塞的 —— 慢订阅者只能丢事件，不能拖住发布者。
"""

from __future__ import annotations

import asyncio

import pytest

from chaoxing.obs.events import Event, EventBus, TaskPointFinished


async def test_publish_reaches_subscriber() -> None:
    """最基本的投递。"""
    bus = EventBus()
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    bus.subscribe(handler)
    bus.publish(TaskPointFinished(account_id=1, point_id="p1", kind="video"))

    assert await bus.wait_idle(timeout=1.0) is True
    assert len(received) == 1
    assert received[0].account_id == 1
    await bus.close()


async def test_publish_is_non_blocking_for_slow_subscribers() -> None:
    """慢订阅者不得阻塞发布者：队列满就丢最旧并计数。"""
    bus = EventBus()
    seen: list[int] = []
    release = asyncio.Event()

    async def slow_handler(event: Event) -> None:
        await release.wait()
        seen.append(event.account_id)

    subscription = bus.subscribe(slow_handler, maxsize=4)

    for index in range(50):
        bus.publish(TaskPointFinished(account_id=index, point_id=f"p{index}"))

    # 发布 50 条没有 await 过任何东西 —— 这就是"非阻塞"的证据
    assert bus.published == 50
    assert subscription.dropped >= 40  # 队列只有 4 个位置

    release.set()
    await bus.wait_idle(timeout=1.0)
    await bus.close()


async def test_broken_subscriber_does_not_break_others() -> None:
    """一个订阅者抛异常，其它订阅者照常收到事件。"""
    bus = EventBus()
    received: list[Event] = []

    async def broken(event: Event) -> None:
        raise RuntimeError("订阅者内部炸了")

    async def healthy(event: Event) -> None:
        received.append(event)

    broken_sub = bus.subscribe(broken)
    bus.subscribe(healthy)
    bus.publish(TaskPointFinished(account_id=1, point_id="p"))

    await bus.wait_idle(timeout=1.0)

    assert received
    assert broken_sub.errors == 1
    await bus.close()


async def test_multiple_subscribers_all_receive() -> None:
    """同一条事件广播给全部订阅者。"""
    bus = EventBus()
    hits: list[str] = []

    def make(tag: str):
        async def handler(event: Event) -> None:
            hits.append(tag)

        return handler

    for tag in ("db", "log", "sse"):
        bus.subscribe(make(tag))
    bus.publish(TaskPointFinished(account_id=1, point_id="p"))

    await bus.wait_idle(timeout=1.0)

    assert sorted(hits) == ["db", "log", "sse"]
    await bus.close()


async def test_close_stops_delivery() -> None:
    """关闭后不再投递，也不会抛异常。"""
    bus = EventBus()
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    bus.subscribe(handler)
    await bus.close()
    bus.publish(TaskPointFinished(account_id=1, point_id="p"))

    assert received == []
    assert bus.stats()["subscribers"] == 0


async def test_stats_reports_counters() -> None:
    """统计接口要能给出发布/处理/丢弃/错误计数（``/system`` 与体检查它）。"""
    bus = EventBus()

    async def handler(event: Event) -> None:
        return None

    bus.subscribe(handler)
    bus.publish(TaskPointFinished(account_id=1, point_id="p"))
    await bus.wait_idle(timeout=1.0)

    stats = bus.stats()

    assert stats["published"] == 1
    assert stats["handled"] == 1
    assert stats["subscribers"] == 1
    await bus.close()


async def test_wait_idle_times_out_when_stuck() -> None:
    """订阅者卡住时 ``wait_idle`` 必须在超时后返回 False（而不是永远挂着）。"""
    bus = EventBus()

    async def stuck(event: Event) -> None:
        await asyncio.sleep(5)

    bus.subscribe(stuck)
    bus.publish(TaskPointFinished(account_id=1, point_id="p"))

    assert await bus.wait_idle(timeout=0.05) is False
    await bus.close()


@pytest.mark.parametrize("kind", ["TaskPointStarted", "TaskPointFinished"])
def test_events_are_frozen(kind: str) -> None:
    """事件是 frozen dataclass：发出后不该被修改（改字段说明设计错了）。"""
    from chaoxing.obs import events as ev

    event = getattr(ev, kind)(account_id=1)

    with pytest.raises((AttributeError, TypeError)):
        event.account_id = 2  # type: ignore[misc]
