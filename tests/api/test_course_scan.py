"""扫描端点的并发防线：同一账号并发扫描必须拿到 ``409 scan_in_progress``。

来源：真机事故 2026-10-14 —— 前端 23 个「扫描任务点」按钮被并发点击，服务端收到 23 条
并发 ``POST /api/courses/points``，直接把账号推进学习通反爬风控。服务层的账号级锁
（``CourseService``）在这里被翻成明确的 409：**不排队**，让第二个请求立刻知道"晚点再来"。

网络用 respx 拦，并留一道闸门把第一个请求**卡在入口请求上** —— 这样第二个请求是真正
与第一个"同时在飞"，不是串行跑完再发。
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import httpx
import respx
from fastapi.testclient import TestClient

from chaoxing.client import SessionState
from chaoxing.core.container import Container
from chaoxing.storage.models import Account, Course

MOOC1 = "https://mooc1.chaoxing.com"
MOOC2_ANS = "https://mooc2-ans.chaoxing.com"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
COURSE_KEY = "265361351:151148348"
CONTENT_HTML = """
<html><body>
<script>var chapterList = [{"chapterId": "1192366344", "chapterName": "第一章 绪论"}];</script>
</body></html>
"""


def seed_account_and_course(client: TestClient) -> int:
    """直接经容器造一个账号 + 一门课（扫任务点要求课程已在本地缓存）。"""
    container: Container = client.app.state.container  # type: ignore[attr-defined]

    async def prepare() -> int:
        async with container.uow.begin() as db:
            account = Account(username="13800000000")
            db.add(account)
            await db.flush()
            db.add(Course(account_id=account.id, course_key=COURSE_KEY, title="测试课"))
            await db.flush()
            return account.id

    return asyncio.run(prepare())


def sign_in(client: TestClient, account_id: int) -> None:
    """给账号注入登录态（没有它就只会拿到 not_logged_in，测不到锁）。"""
    container: Container = client.app.state.container  # type: ignore[attr-defined]
    container.registry.put(SessionState(account_id=account_id, cookies={"_uid": "100000005"}))


def _mock_scan_path(side_effect: object) -> None:
    """课程入口用给定的 ``side_effect``（闸门），章节页与卡片给固定响应。"""
    respx.get(f"{MOOC1}/mooc-ans/visit/stucoursemiddle").mock(side_effect=side_effect)
    respx.get(url__startswith=f"{MOOC2_ANS}/mooc2-ans/mycourse/studentcourse").mock(
        return_value=httpx.Response(200, text=CONTENT_HTML)
    )
    respx.get(url__startswith=f"{MOOC1}/knowledge/cards").mock(
        return_value=httpx.Response(200, text=(FIXTURES / "job_card_video.json").read_text("utf-8"))
    )


@respx.mock
def test_concurrent_scan_points_returns_conflict(logged_in: TestClient) -> None:
    """并发两次 ``POST /api/courses/points``：第二次 409 且 ``code=scan_in_progress``。"""
    client = logged_in
    account_id = seed_account_and_course(client)
    sign_in(client, account_id)
    gate = asyncio.Event()
    entered = threading.Event()

    async def slow_entry(request: httpx.Request) -> httpx.Response:
        entered.set()
        await gate.wait()
        return httpx.Response(
            302,
            headers={"location": f"{MOOC2_ANS}/mooc2-ans/mycourse/studentcourse?cpi=1&stuenc=x"},
        )

    _mock_scan_path(slow_entry)
    body = {"account_id": account_id, "course_key": COURSE_KEY}
    first: list[httpx.Response] = []

    thread = threading.Thread(
        target=lambda: first.append(client.post("/api/courses/points", json=body))
    )
    thread.start()
    try:
        assert entered.wait(timeout=10), "第一个扫描没有进入被卡住的入口请求"

        second = client.post("/api/courses/points", json=body)

        assert second.status_code == 409, second.text
        assert second.json()["error"]["code"] == "scan_in_progress"
    finally:
        client.portal.call(gate.set)  # type: ignore[union-attr]
        thread.join(timeout=20)

    assert first, "第一个请求没有返回"
    assert first[0].status_code == 200, first[0].text


def test_scan_courses_returns_conflict_when_lock_held(logged_in: TestClient) -> None:
    """「扫描课程」共用同一把账号锁：锁被占用时也返回 409（而不是排队等）。"""
    client = logged_in
    container: Container = client.app.state.container  # type: ignore[attr-defined]
    account_id = seed_account_and_course(client)
    lock = container.courses._scan_lock(account_id)
    client.portal.call(lock.acquire)  # type: ignore[union-attr]
    try:
        response = client.post("/api/courses/scan", json={"account_id": account_id})
    finally:
        client.portal.call(lock.release)  # type: ignore[union-attr]

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "scan_in_progress"
