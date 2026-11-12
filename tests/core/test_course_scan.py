"""任务点扫描：从课程入口一路走到任务点入库。

来源：legacy 的关键经验 —— 章节页必须带 ``stuenc``（课程入口 302 时签发），
少了它服务端直接给 ``[3004] 长时间没有操作或没有权限``；卡片里的 ``_jobid``
藏在 ``property`` 里，解析层负责摊平，入库时必须带上，否则心跳永远不会被确认。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select

from chaoxing.client import ChaoxingTransport, SessionRegistry
from chaoxing.config import ChaoxingSettings
from chaoxing.core.services.courses import (
    CourseNotFound,
    CourseService,
    NotLoggedIn,
    ScanInProgress,
)
from chaoxing.obs.events import EventBus
from chaoxing.storage.models import TaskPoint
from chaoxing.storage.uow import UnitOfWork

MOOC1 = "https://mooc1.chaoxing.com"
MOOC2_ANS = "https://mooc2-ans.chaoxing.com"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
COURSE_KEY = "265361351:151148348"

CONTENT_HTML = """
<html><body>
<script>var chapterList = [{"chapterId": "1192366344", "chapterName": "第一章 绪论"}];</script>
</body></html>
"""


@pytest.fixture
async def service(uow: UnitOfWork) -> AsyncIterator[tuple[CourseService, SessionRegistry]]:
    """课程服务 + 会话注册表。"""
    transport = ChaoxingTransport(ChaoxingSettings(request_min_interval=0, max_retry=0))
    registry = SessionRegistry()
    bus = EventBus()
    instance = CourseService(uow=uow, transport=transport, registry=registry, bus=bus)
    yield instance, registry
    await bus.close()
    await transport.aclose()


async def seed_account(uow: UnitOfWork, username: str) -> int:
    """造一个账号 + 一门本地课程缓存（多账号用例靠 ``username`` 区分）。"""
    from chaoxing.storage.models import Account, Course

    async with uow.begin() as session:
        account = Account(username=username)
        session.add(account)
        await session.flush()
        session.add(Course(account_id=account.id, course_key=COURSE_KEY, title="测试课"))
        await session.flush()
        return account.id


async def seed_course(uow: UnitOfWork) -> int:
    """造一条本地课程缓存 + 一个已登录账号。"""
    return await seed_account(uow, "13800000000")


def sign_in(registry: SessionRegistry, account_id: int) -> None:
    """给账号注入登录态。"""
    registry.get(account_id).cookies["_uid"] = "100000005"


@respx.mock
async def test_scan_points_walks_entry_to_task_points(
    uow: UnitOfWork, service: tuple[CourseService, SessionRegistry]
) -> None:
    """完整链路：入口换 stuenc → 章节页 → 卡片 → 入库（payload 透传字段齐全）。"""
    courses, registry = service
    account_id = await seed_course(uow)
    sign_in(registry, account_id)

    respx.get(f"{MOOC1}/mooc-ans/visit/stucoursemiddle").mock(
        return_value=httpx.Response(
            302,
            headers={
                "location": f"{MOOC2_ANS}/mooc2-ans/mycourse/studentcourse?courseId=265361351&clazzid=151148348"
                "&cpi=507072787&stuenc=abc123"
            },
        )
    )
    respx.get(url__startswith=f"{MOOC2_ANS}/mooc2-ans/mycourse/studentcourse").mock(
        return_value=httpx.Response(200, text=CONTENT_HTML)
    )
    respx.get(url__startswith=f"{MOOC1}/knowledge/cards").mock(
        return_value=httpx.Response(
            200, text=json.dumps(json.loads((FIXTURES / "job_card_video.json").read_text("utf-8")))
        )
    )

    report = await courses.scan_points(account_id, COURSE_KEY)

    assert report.chapters == 1
    assert report.total >= 1, "一个任务点都没入库"

    async with uow.read() as session:
        points = list((await session.scalars(select(TaskPoint))).all())
    video = next(point for point in points if point.kind == "video")
    assert video.job_id, "jobid 没取到（_jobid 在 property 里，摊平了吗？）"
    assert video.payload["otherInfo"], "otherInfo 没存进 payload —— 心跳就发不出去"
    assert video.payload["clazzId"] == "151148348"
    assert video.payload["courseId"] == "265361351"
    assert video.sequence >= 0


@respx.mock
async def test_rescan_is_idempotent(
    uow: UnitOfWork, service: tuple[CourseService, SessionRegistry]
) -> None:
    """重复扫描不产生重复任务点（重扫只更新，不新增）。"""
    courses, registry = service
    account_id = await seed_course(uow)
    sign_in(registry, account_id)
    respx.get(f"{MOOC1}/mooc-ans/visit/stucoursemiddle").mock(
        return_value=httpx.Response(
            302,
            headers={
                "location": f"{MOOC2_ANS}/mooc2-ans/mycourse/studentcourse?cpi=507072787&stuenc=abc123"
            },
        )
    )
    respx.get(url__startswith=f"{MOOC2_ANS}/mooc2-ans/mycourse/studentcourse").mock(
        return_value=httpx.Response(200, text=CONTENT_HTML)
    )
    respx.get(url__startswith=f"{MOOC1}/knowledge/cards").mock(
        return_value=httpx.Response(200, text=(FIXTURES / "job_card_video.json").read_text("utf-8"))
    )

    first = await courses.scan_points(account_id, COURSE_KEY)
    second = await courses.scan_points(account_id, COURSE_KEY)

    assert first.created > 0
    assert second.created == 0
    assert second.updated == first.created + first.updated

    async with uow.read() as session:
        points = list((await session.scalars(select(TaskPoint))).all())
    assert len(points) == first.created + first.updated


async def test_scan_points_requires_login(
    uow: UnitOfWork, service: tuple[CourseService, SessionRegistry]
) -> None:
    """没有登录态时明确报错，且不发请求。"""
    courses, _ = service
    account_id = await seed_course(uow)

    with pytest.raises(NotLoggedIn):
        await courses.scan_points(account_id, COURSE_KEY)


@respx.mock
async def test_scan_points_requires_local_course(
    uow: UnitOfWork, service: tuple[CourseService, SessionRegistry]
) -> None:
    """本地没有这门课时给出可行动的提示（先去 scan 课程列表）。"""
    courses, registry = service
    account_id = await seed_course(uow)
    sign_in(registry, account_id)

    with pytest.raises(CourseNotFound, match="cx course scan"):
        await courses.scan_points(account_id, "999:888")

    # 异常路径也必须释放锁：否则这个账号的扫描会被永久卡死（第二次会变成 ScanInProgress）
    with pytest.raises(CourseNotFound, match="cx course scan"):
        await courses.scan_points(account_id, "999:888")


@respx.mock
async def test_chapter_page_without_chapters_reports_warning(
    uow: UnitOfWork, service: tuple[CourseService, SessionRegistry]
) -> None:
    """页面结构变了导致一章都解析不到时，要给出警告而不是静默成功。"""
    courses, registry = service
    account_id = await seed_course(uow)
    sign_in(registry, account_id)
    respx.get(f"{MOOC1}/mooc-ans/visit/stucoursemiddle").mock(
        return_value=httpx.Response(
            302,
            headers={"location": f"{MOOC2_ANS}/mooc2-ans/mycourse/studentcourse?cpi=1&stuenc=x"},
        )
    )
    respx.get(url__startswith=f"{MOOC2_ANS}/mooc2-ans/mycourse/studentcourse").mock(
        return_value=httpx.Response(200, text="<html><body>空页面</body></html>")
    )

    report = await courses.scan_points(account_id, COURSE_KEY)

    assert report.chapters == 0
    assert report.warnings
    assert report.total == 0


#: 测验卡片 + 章节页（页面里带隐藏的 ``enc``，供 ``_fetch_utenc`` 用）
QUIZ_CARD = {
    "defaults": {
        "ktoken": "fresh-ktoken",
        "userid": "100000002",
        "cpi": "507072787",
        "clazzId": "151148348",
        "cardid": "1188676071",
    },
    "attachments": [
        {
            "type": "work",
            "jobid": "job-quiz-1",
            "workid": "0f9c1e2d3a4b5c6d7e8f90a1b2c3d4e5",
            "enc": "aabbccddeeff00112233445566778899",
            "jtoken": "fresh-jtoken",
            "property": {"_jobid": "job-quiz-1", "name": "课后测验"},
        }
    ],
}
QUIZ_CONTENT_HTML = """
<html><body>
<input id="enc" value="aabbccddeeff00112233445566778899">
<script>var chapterList = [{"chapterId": "1192366344", "chapterName": "第一章 绪论"}];</script>
</body></html>
"""
FRESH_UTENC = "ffeeddccbbaa99887766554433221100"


def _mock_quiz_scan() -> None:
    """复刻一次"进课 → 章节页 → 测验卡片"的成套响应。"""
    respx.get(f"{MOOC1}/mooc-ans/visit/stucoursemiddle").mock(
        return_value=httpx.Response(
            302,
            headers={
                "location": f"{MOOC2_ANS}/mooc2-ans/mycourse/studentcourse?courseId=265361351"
                "&clazzid=151148348&cpi=507072787&stuenc=abc123"
            },
        )
    )
    respx.get(url__startswith=f"{MOOC2_ANS}/mooc2-ans/mycourse/studentcourse").mock(
        return_value=httpx.Response(200, text=QUIZ_CONTENT_HTML)
    )
    respx.get(url__startswith=f"{MOOC1}/knowledge/cards").mock(
        return_value=httpx.Response(200, text=json.dumps(QUIZ_CARD))
    )
    respx.get(url__startswith=f"{MOOC1}/mycourse/studentstudy").mock(
        return_value=httpx.Response(200, text=f'<script>var utEnc="{FRESH_UTENC}";</script>')
    )


@respx.mock
async def test_refresh_quiz_tokens_returns_fresh_card_values(
    uow: UnitOfWork, service: tuple[CourseService, SessionRegistry]
) -> None:
    """就地重扫卡片：返回值是**新鲜**的（与扫描时存的旧值不同），字段来源与入库一致。

    真机实测：卡片里的 ``enc`` 几分钟就轮换 —— 扫描时存下的值用于提交会被拒。
    """
    courses, registry = service
    account_id = await seed_course(uow)
    sign_in(registry, account_id)
    _mock_quiz_scan()

    fresh = await courses.refresh_quiz_tokens(
        account_id,
        course_id="265361351",
        clazz_id="151148348",
        chapter_id="1192366344",
        work_id="0f9c1e2d3a4b5c6d7e8f90a1b2c3d4e5",
    )

    assert fresh["enc"] == "aabbccddeeff00112233445566778899"
    assert fresh["enc"] != "old-scan-enc", "必须取新页面的 enc，不能沿用扫描时的值"
    assert fresh["jtoken"] == "fresh-jtoken"
    assert fresh["ktoken"] == "fresh-ktoken"
    assert fresh["cpi"] == "507072787"
    assert fresh["clazzId"] == "151148348"
    assert fresh["userid"] == "100000002"
    assert fresh["cardid"] == "1188676071"
    assert fresh["workid"] == "0f9c1e2d3a4b5c6d7e8f90a1b2c3d4e5"
    assert fresh["utEnc"] == FRESH_UTENC


@respx.mock
async def test_refresh_quiz_tokens_returns_empty_when_card_missing(
    uow: UnitOfWork, service: tuple[CourseService, SessionRegistry]
) -> None:
    """卡片里找不到 workid（页面变了 / 任务点没了）时返回空字典，绝不抛异常。"""
    courses, registry = service
    account_id = await seed_course(uow)
    sign_in(registry, account_id)
    _mock_quiz_scan()

    fresh = await courses.refresh_quiz_tokens(
        account_id,
        course_id="265361351",
        clazz_id="151148348",
        chapter_id="1192366344",
        work_id="这份 workid 不在卡片里",
    )

    assert fresh == {}


@respx.mock
async def test_refresh_quiz_tokens_swallows_errors(
    uow: UnitOfWork, service: tuple[CourseService, SessionRegistry]
) -> None:
    """没有登录态等任何失败都返回空字典（失败要软，调用方拿旧 payload 继续跑）。"""
    courses, _ = service
    account_id = await seed_course(uow)  # 故意不 sign_in

    fresh = await courses.refresh_quiz_tokens(
        account_id,
        course_id="265361351",
        clazz_id="151148348",
        chapter_id="1192366344",
        work_id="0f9c1e2d3a4b5c6d7e8f90a1b2c3d4e5",
    )

    assert fresh == {}


# --------------------------------------------------------------------------- #
# 扫描互斥（真机事故 2026-10-14：23 个按钮并发点 → 学习通风控）
# --------------------------------------------------------------------------- #
def _mock_scan_path(side_effect: object) -> None:
    """给"进课 → 章节页 → 卡片"配一套响应；入口用给定的 ``side_effect``（供阻塞）。"""
    respx.get(f"{MOOC1}/mooc-ans/visit/stucoursemiddle").mock(side_effect=side_effect)
    respx.get(url__startswith=f"{MOOC2_ANS}/mooc2-ans/mycourse/studentcourse").mock(
        return_value=httpx.Response(200, text=CONTENT_HTML)
    )
    respx.get(url__startswith=f"{MOOC1}/knowledge/cards").mock(
        return_value=httpx.Response(200, text=(FIXTURES / "job_card_video.json").read_text("utf-8"))
    )


@respx.mock
async def test_concurrent_scan_same_account_is_rejected(
    uow: UnitOfWork, service: tuple[CourseService, SessionRegistry]
) -> None:
    """同一账号并发扫描：第二个直接被拒（``ScanInProgress``），不排队、不并发打学习通。"""
    courses, registry = service
    account_id = await seed_course(uow)
    sign_in(registry, account_id)
    gate = asyncio.Event()
    entered = asyncio.Event()

    async def slow_entry(request: httpx.Request) -> httpx.Response:
        entered.set()
        await gate.wait()
        return httpx.Response(
            302,
            headers={"location": f"{MOOC2_ANS}/mooc2-ans/mycourse/studentcourse?cpi=1&stuenc=x"},
        )

    _mock_scan_path(slow_entry)

    first = asyncio.create_task(courses.scan_points(account_id, COURSE_KEY))
    await asyncio.wait_for(entered.wait(), timeout=5)

    with pytest.raises(ScanInProgress):
        await courses.scan_points(account_id, COURSE_KEY)

    gate.set()
    report = await first
    assert report.total >= 1

    # 锁必须被释放（异常路径也不例外）：同一个账号可以立刻再扫一次
    again = await courses.scan_points(account_id, COURSE_KEY)
    assert again.created == 0
    assert again.updated == report.total


@respx.mock
async def test_concurrent_scan_different_accounts_do_not_interfere(
    uow: UnitOfWork, service: tuple[CourseService, SessionRegistry]
) -> None:
    """不同账号各有各的锁：两边可以同时进行（不会被互相误伤成 409）。"""
    courses, registry = service
    first_account = await seed_course(uow)
    second_account = await seed_account(uow, "13800000001")
    sign_in(registry, first_account)
    sign_in(registry, second_account)
    gate = asyncio.Event()
    both_entered = asyncio.Event()
    entered = 0

    async def slow_entry(request: httpx.Request) -> httpx.Response:
        nonlocal entered
        entered += 1
        if entered == 2:
            both_entered.set()
        await gate.wait()
        return httpx.Response(
            302,
            headers={"location": f"{MOOC2_ANS}/mooc2-ans/mycourse/studentcourse?cpi=1&stuenc=x"},
        )

    _mock_scan_path(slow_entry)

    first = asyncio.create_task(courses.scan_points(first_account, COURSE_KEY))
    second = asyncio.create_task(courses.scan_points(second_account, COURSE_KEY))
    await asyncio.wait_for(both_entered.wait(), timeout=5)
    assert entered == 2

    gate.set()
    reports = await asyncio.gather(first, second)
    assert all(report.total >= 1 for report in reports)
