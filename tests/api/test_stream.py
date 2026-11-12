"""SSE 实时通道：序列化、事件名、退订清理，以及 app 日志流（``/api/stream/logs``）。

日志流的用例**不用** ``TestClient``：它的传输层会把整个响应体缓冲到内存
（``portal.call(app)`` 等 app 返回才结束），对着无限流会永久挂住。这里直接驱动 ASGI
（自己喂 ``send`` / ``receive``），既真跑路由与鉴权，又能在拿到目标记录后立刻断开 ——
顺带钉住"断连即退订"。
"""

from __future__ import annotations

import asyncio
import gc
import hashlib
import json
import logging
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaoxing.api.app import create_app
from chaoxing.api.routers.stream.routes import LOG_EVENT_NAME, event_name, serialize
from chaoxing.api.security import SESSION_COOKIE
from chaoxing.config import Settings
from chaoxing.core.container import Container
from chaoxing.obs.events import EventBus, TaskPointFinished, TaskPointProgress
from chaoxing.obs.log_stream import LogBroadcaster, LogRecord, log_broadcaster
from chaoxing.obs.logging import setup_logging
from chaoxing.storage.engine import create_all, create_engine


def test_event_name_is_the_class_name() -> None:
    """事件名前端按它分类处理，因此必须是稳定的类名。"""
    assert event_name(TaskPointFinished(account_id=1)) == "TaskPointFinished"
    assert event_name(TaskPointProgress(account_id=1)) == "TaskPointProgress"


def test_serialize_emits_iso_datetime() -> None:
    """``occurred_at`` 要转成 ISO 字符串（前端 ``new Date()`` 直接能解析）。"""
    payload = json.loads(serialize(TaskPointFinished(account_id=1, point_id="p", kind="video")))

    assert isinstance(payload["occurred_at"], str)
    assert "T" in payload["occurred_at"]
    assert payload["account_id"] == 1
    assert payload["kind"] == "video"


def test_serialize_keeps_chinese_readable() -> None:
    """中文不转义 —— 前端直接显示，排障时日志也可读。"""
    text = serialize(TaskPointFinished(account_id=1, detail="任务点已完成"))

    assert "任务点已完成" in text
    assert "\\u" not in text


async def test_unsubscribe_stops_delivery() -> None:
    """退订之后不再收到事件（SSE 客户端断连时会走这条路）。"""
    bus = EventBus()
    received: list[object] = []

    async def handler(event: object) -> None:
        received.append(event)

    subscription = bus.subscribe(handler)
    bus.publish(TaskPointFinished(account_id=1))
    await bus.wait_idle(timeout=1.0)
    assert len(received) == 1

    bus.unsubscribe(subscription)
    bus.publish(TaskPointFinished(account_id=2))
    await asyncio.sleep(0.01)

    assert len(received) == 1, "退订之后仍在投递"
    assert bus.stats()["subscribers"] == 0


async def test_unsubscribe_is_idempotent() -> None:
    """重复退订不报错（断连路径上容错比严格更重要）。"""
    bus = EventBus()

    async def handler(event: object) -> None:
        return None

    subscription = bus.subscribe(handler)
    bus.unsubscribe(subscription)
    bus.unsubscribe(subscription)

    assert bus.stats()["subscribers"] == 0


# --------------------------------------------------------------------------- #
# app 日志流（GET /api/stream/logs）
# --------------------------------------------------------------------------- #

LOGS_PATH = "/api/stream/logs"

#: 单个 SSE 分片的最长等待（秒）—— 失败必须是断言失败，不能把用例挂死
STREAM_TIMEOUT = 5.0

#: 用例里用来打哨兵日志的 logger 名
SENTINEL_LOGGER = "chaoxing.api.test_stream"


def _record(event: str, *, level: str = "info") -> LogRecord:
    """造一条记录（只测广播器时用，不经 structlog）。"""
    return LogRecord(
        level=level, logger="chaoxing.test", event=event, timestamp="2026-01-01T00:00:00Z"
    )


def test_slow_subscriber_drops_oldest_and_counts() -> None:
    """订阅者满了丢最旧并计数：写日志的一方绝不等消费者。"""
    broadcaster = LogBroadcaster(history=10)
    subscription = broadcaster.subscribe(maxsize=2, min_level="debug")

    for index in range(5):
        broadcaster.publish(_record(f"第 {index} 条"))

    assert subscription.dropped == 3
    assert [item.event for item in subscription.drain()] == ["第 3 条", "第 4 条"]
    assert broadcaster.stats()["dropped"] == 3

    broadcaster.unsubscribe(subscription)
    assert broadcaster.stats()["subscribers"] == 0
    # 退订之后不再投递（否则慢客户端的队列会被一直填）
    broadcaster.publish(_record("退订之后"))
    assert subscription.dropped == 3


def test_subscribe_replays_only_the_tail() -> None:
    """新连接先回放缓冲尾部（backlog），并且按级别过滤。"""
    broadcaster = LogBroadcaster(history=10)
    broadcaster.publish(_record("普通一条"))
    broadcaster.publish(_record("警告一条", level="warning"))

    subscription = broadcaster.subscribe(min_level="warning", backlog=5)

    assert [item.event for item in subscription.drain()] == ["警告一条"]
    broadcaster.unsubscribe(subscription)


def test_stdlib_record_is_broadcast_once(tmp_path: Path) -> None:
    """同一条 stdlib 日志只广播一次。

    实测缺陷（2026-10-11）：`ProcessorFormatter` 会给**每个 handler** 各跑一遍
    `foreign_pre_chain`（本仓库是控制台 + 文件两个 handler），于是同一次请求在面板里
    出现两行。修复是按 `LogRecord` 去重，这条用例把它钉住。
    """
    setup_logging(level="INFO", log_dir=tmp_path, json_output=True)
    subscription = log_broadcaster.subscribe(maxsize=8, min_level="debug", backlog=0)
    try:
        logging.getLogger(SENTINEL_LOGGER).info("去重哨兵")
        records = subscription.drain()
    finally:
        log_broadcaster.unsubscribe(subscription)

    assert [item.event for item in records if item.event == "去重哨兵"] == ["去重哨兵"]


async def test_subscription_wakes_on_new_record() -> None:
    """实时推送：订阅之后产生的记录立刻到达，不用等保活超时。"""
    broadcaster = LogBroadcaster(history=1)
    subscription = broadcaster.subscribe(maxsize=4, min_level="debug", backlog=0)

    async def publish_later() -> None:
        await asyncio.sleep(0)  # 让消费端先进入等待
        broadcaster.publish(_record("实时一条"))

    task = asyncio.create_task(publish_later())
    batch = await subscription.wait(2.0)
    await task

    assert [item.event for item in batch] == ["实时一条"]
    broadcaster.unsubscribe(subscription)


class _Sink:
    """收集 ASGI 响应分片，并提供"客户端还没断开"的 ``receive``。"""

    def __init__(self) -> None:
        self.status = 0
        self.chunks: list[bytes] = []
        self.arrived = asyncio.Event()
        self.finished = asyncio.Event()

    async def send(self, message: dict[str, Any]) -> None:
        """收下一个响应分片。"""
        if message["type"] == "http.response.start":
            self.status = int(message["status"])
            return
        body = message.get("body", b"")
        if body:
            self.chunks.append(body)
            self.arrived.set()
        if not message.get("more_body", False):
            self.finished.set()

    async def receive(self) -> dict[str, Any]:
        """真实服务器在客户端断开前一直阻塞在这里；断开由用例取消任务承担。"""
        await self.finished.wait()
        return {"type": "http.disconnect"}

    def take(self) -> str:
        """取走已到达的分片（解码成文本）。"""
        self.arrived.clear()
        text = b"".join(self.chunks).decode()
        self.chunks.clear()
        return text


def _sse_frames(text: str) -> list[tuple[str, str]]:
    """把 SSE 原文切成 ``(事件名, data)``；注释（保活）与没收全的尾帧直接丢掉。"""
    frames: list[tuple[str, str]] = []
    for block in text.replace("\r\n", "\n").split("\n\n")[:-1]:
        name = ""
        data: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data.append(line[len("data:") :].strip())
        if name and data:
            frames.append((name, "\n".join(data)))
    return frames


async def _read_log_stream(
    app: FastAPI, *, cookie: str, query: str, until: Callable[[dict[str, Any]], bool]
) -> list[dict[str, Any]]:
    """连上日志流读到 ``until`` 命中即断开，返回收到并解析过的日志记录。

    断开走真的 cancel —— 这样生成器 ``finally`` 里的退订才会被执行。
    """
    sink = _Sink()
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": LOGS_PATH,
        "raw_path": LOGS_PATH.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"cookie", f"{SESSION_COOKIE}={cookie}".encode()),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "state": {},
    }
    task = asyncio.create_task(app(scope, sink.receive, sink.send))
    text = ""
    records: list[dict[str, Any]] = []
    try:
        while True:
            try:
                await asyncio.wait_for(sink.arrived.wait(), STREAM_TIMEOUT)
            except TimeoutError:
                break  # 超时不再等：让断言去解释收到了什么，而不是挂死用例
            text += sink.take()
            records = [
                json.loads(data) for name, data in _sse_frames(text) if name == LOG_EVENT_NAME
            ]
            if any(until(record) for record in records):
                break
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert sink.status == 200, f"日志流没起来（状态码 {sink.status}）"
    return records


async def _wait_unsubscribed(*, timeout: float = 1.0) -> bool:
    """等日志广播器把订阅放掉（断连即退订）。

    取消 ASGI 任务之后，生成器的 ``finally`` 要等它被回收才会跑 —— asyncio 的 async
    generator 终结器把 ``aclose()`` 排到下一个循环周期，而这里没人再引用那个生成器
    （它挂在被取消的任务留下的引用环里）。所以显式推一次回收，把"断连即退订"钉成
    机器判据，而不是等某个不确定的 GC 时机。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    if log_broadcaster.stats()["subscribers"] == 0:
        return True
    gc.collect()
    while log_broadcaster.stats()["subscribers"] != 0:
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(0.01)
    return True


@pytest.fixture
async def live_app(settings: Settings) -> AsyncIterator[FastAPI]:
    """在**当前**事件循环里跑 lifespan 的应用（SSE 用例要自己控制 send/receive）。"""
    engine = create_engine(settings.db_path)
    await create_all(engine)
    await engine.dispose()

    app = create_app(settings)
    async with app.router.lifespan_context(app):
        yield app


async def _open_session(app: FastAPI, *, username: str = "admin") -> str:
    """直接在容器里造用户 + 会话，返回会话 token（省掉注册时的 nonce 往返）。"""
    container: Container = app.state.container
    user = await container.users.create(
        username=username,
        client_secret=hashlib.sha256(b"passw0rd").hexdigest(),
        is_superuser=True,
    )
    return await container.users.open_session(user)


async def test_logs_stream_delivers_app_log_lines(live_app: FastAPI) -> None:
    """有 `system:logs` 时能收到 `log` 事件，字段与 `GET /api/logs` 一致。"""
    token = await _open_session(live_app)
    marker = f"日志流哨兵-{uuid4().hex}"
    logging.getLogger(SENTINEL_LOGGER).info(marker)

    records = await _read_log_stream(
        live_app,
        cookie=token,
        query="min_level=info&backlog=50",
        until=lambda record: record["event"] == marker,
    )

    line = next(record for record in records if record["event"] == marker)
    assert line["level"] == "info"
    assert line["logger"] == SENTINEL_LOGGER
    assert "T" in line["timestamp"] and line["timestamp"].endswith("Z")
    assert set(line) == {"level", "logger", "event", "timestamp"}, "多带了字段（可能泄漏凭据）"
    assert await _wait_unsubscribed(), "客户端断开后没有退订（慢订阅者的队列会被一直填）"


async def test_logs_stream_respects_min_level(live_app: FastAPI) -> None:
    """`min_level=error`：info 行根本进不来，error 行照收。"""
    token = await _open_session(live_app)
    quiet = f"info-不该出现-{uuid4().hex}"
    loud = f"error-该出现-{uuid4().hex}"
    sentinel = logging.getLogger(SENTINEL_LOGGER)
    sentinel.info(quiet)
    sentinel.error(loud)

    records = await _read_log_stream(
        live_app,
        cookie=token,
        query="min_level=error&backlog=200",
        until=lambda record: record["event"] == loud,
    )

    assert any(record["event"] == loud for record in records)
    assert all(record["level"] in {"error", "critical"} for record in records)
    assert all(quiet not in record["event"] for record in records)


def test_logs_stream_requires_permission(client: TestClient, registered: dict[str, str]) -> None:
    """没有 `system:logs` 的用户开不了日志流（与 `GET /api/logs` 同一节点）。"""
    secret = hashlib.sha256(b"passw0rd").hexdigest()
    nonce = client.get("/api/auth/challenge", params={"scene": "register"}).json()["nonce"]
    client.post(
        "/api/auth/register",
        json={"username": "looker", "secret": secret, "nonce": nonce},
    )
    client.post("/api/auth/login", json={"username": "looker", "secret": secret})

    response = client.get(LOGS_PATH)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
