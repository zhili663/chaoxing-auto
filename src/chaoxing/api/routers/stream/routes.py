"""SSE 实时通道（``docs/REFACTOR_PLAN.md`` §6：实时数据与日志都走 SSE）。

两条流，故意不合并 —— 领域事件答"发生了什么业务动作"，app 日志答"服务端怎么想的"：

* ``GET /api/events`` —— 领域事件（``EventBus``，登录用户即可订阅）；
* ``GET /api/stream/logs`` —— app 日志（``obs.log_stream`` 的广播器，要 ``system:logs``）。

三条设计约束：

* **同一个 Cookie 鉴权** —— ``EventSource`` 不能自定义请求头，用 Cookie 才能直接用；
* **保活用服务端超时，不用 ``setInterval``** —— 前端不轮询，服务端在没有事件的间隙
  发一行注释（``: keepalive``）即可，SSE 规范里注释行会被客户端忽略但不影响连接存活；
* **断连即退订** —— 客户端走人时立刻取消订阅，否则慢订阅者的有界队列会一直白丢事件。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import asdict, is_dataclass
from typing import Any

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from chaoxing.api.deps import AuthContext, get_container, require
from chaoxing.core.container import Container
from chaoxing.obs.events import Event
from chaoxing.obs.log_stream import HISTORY_LIMIT, log_broadcaster

log = logging.getLogger(__name__)

router = APIRouter(tags=["stream"])

#: 没有事件时的保活间隔（秒）
KEEPALIVE_SECONDS = 15
#: 每个连接的缓冲上限（满了丢最旧 —— 慢客户端不能拖垮发布者）
CLIENT_BUFFER = 128
#: 日志流默认回放多少条尾部
DEFAULT_BACKLOG = 50
#: 日志 SSE 的事件名（前端按它 ``addEventListener``）
LOG_EVENT_NAME = "log"


def event_name(event: Event) -> str:
    """事件名（前端用 ``addEventListener`` 按它分类处理）。"""
    return type(event).__name__


def serialize(event: Event) -> str:
    """把事件序列化成 JSON 文本。

    ``occurred_at`` 是 datetime，统一转 ISO 字符串 —— 前端拿到就能直接 ``new Date()``。
    """
    payload: dict[str, Any] = {}
    if is_dataclass(event) and not isinstance(event, type):
        for key, value in asdict(event).items():
            payload[key] = value.isoformat() if hasattr(value, "isoformat") else value
    return json.dumps(payload, ensure_ascii=False)


async def event_stream(request: Request, container: Container) -> AsyncIterator[dict[str, str]]:
    """把事件总线转成 SSE 流。"""
    queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=CLIENT_BUFFER)
    dropped = 0

    async def handler(event: Event) -> None:
        nonlocal dropped
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            dropped += 1

    subscription = container.bus.subscribe(handler, maxsize=CLIENT_BUFFER)
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
            except TimeoutError:
                # 注释行保活：客户端会忽略它，但连接因此不会因空闲被中间层掐断
                yield {"comment": "keepalive"}
                continue
            yield {"event": event_name(event), "data": serialize(event)}
    finally:
        subscription.task and subscription.task.cancel()
        container.bus.unsubscribe(subscription)
        log.debug("SSE 连接关闭")


@router.get("/events")
async def stream_events(
    request: Request,
    ctx: AuthContext = Depends(require("stream:subscribe")),
    container: Container = Depends(get_container),
) -> EventSourceResponse:
    """事件流（登录用户可订阅；管理员会额外收到全局事件）。"""
    log.info("SSE 已连接：用户 %s", ctx.user.username)
    return EventSourceResponse(event_stream(request, container))


async def log_event_stream(
    request: Request, *, min_level: str, backlog: int
) -> AsyncIterator[dict[str, str]]:
    """把日志广播器转成 SSE 流：先回放 ``backlog`` 条尾部，再推实时。

    骨架与 :func:`event_stream` 一致（保活注释 + 断连退订），只是数据源是**同步**的
    日志广播器，所以"等记录"由订阅者自己完成。
    """
    subscription = log_broadcaster.subscribe(
        maxsize=CLIENT_BUFFER, min_level=min_level, backlog=backlog
    )
    try:
        while True:
            if await request.is_disconnected():
                break
            batch = await subscription.wait(KEEPALIVE_SECONDS)
            if not batch:
                # 注释行保活：客户端会忽略它，但连接因此不会因空闲被中间层掐断
                yield {"comment": "keepalive"}
                continue
            for record in batch:
                # 记录里只有 level/logger/event/timestamp —— 日志本就不写明文凭据
                yield {
                    "event": LOG_EVENT_NAME,
                    "data": json.dumps(record.as_dict(), ensure_ascii=False),
                }
    finally:
        log_broadcaster.unsubscribe(subscription)


@router.get("/stream/logs")
async def stream_logs(
    request: Request,
    min_level: str = "info",
    backlog: int = DEFAULT_BACKLOG,
    _ctx: AuthContext = Depends(require("system:logs")),
) -> EventSourceResponse:
    """app 日志流（`system:logs`，与 `GET /api/logs` 同一节点）。

    :param min_level: 级别门槛（debug/info/warning/error，不认识的按 info 处理）
    :param backlog: 先回放尾部多少条（上限见 :data:`HISTORY_LIMIT`）
    """
    return EventSourceResponse(
        log_event_stream(
            request,
            min_level=min_level.lower(),
            backlog=max(0, min(backlog, HISTORY_LIMIT)),
        )
    )
