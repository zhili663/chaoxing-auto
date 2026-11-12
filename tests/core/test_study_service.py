"""刷课编排：视频心跳推进、回执判定、不拉流、失败与人工介入。

来源：legacy 的核心工程约束 ——
① 只发心跳、**绝不请求 m3u8/ts/mp4**；② 完成以服务端回执为准（宁可失败，不谎报）；
③ 时长/进度推进必须与上报的 rt 一致。
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from sqlalchemy import select

from chaoxing.client import ChaoxingTransport, SessionRegistry
from chaoxing.client.exceptions import EnvelopeFailure
from chaoxing.client.model.quiz import Answer, Question
from chaoxing.config import ChaoxingSettings, Settings
from chaoxing.core.services import AnswerEngine
from chaoxing.core.services.courses import ProgressSyncReport
from chaoxing.core.services.study import (
    RETRY_LIMIT,
    PointView,
    StudyReport,
    StudyService,
    _looks_like_work_url,
)
from chaoxing.obs.account_log import AccountLogWriter
from chaoxing.obs.events import EventBus, TaskPointProgress
from chaoxing.storage.models import (
    RUN_PAUSE_REQUESTED,
    RUN_PAUSED,
    RUN_RESUME_REQUESTED,
    RUN_RUNNING,
    RUN_STOP_REQUESTED,
    Account,
    Course,
    StudySession,
    TaskPoint,
)
from chaoxing.storage.uow import UnitOfWork

MOOC1 = "https://mooc1.chaoxing.com"


async def no_wait(_seconds: float) -> None:
    """零等待：测试里不真的等 12~20 秒的心跳间隔。"""
    await asyncio.sleep(0)


@pytest.fixture
def settings() -> Settings:
    """测试配置：心跳间隔压到极小、不限速、不重试。"""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        secret_key="x" * 43 + "=",
        chaoxing=ChaoxingSettings(
            request_min_interval=0,
            max_retry=0,
            heartbeat_interval_min=0.001,
            heartbeat_interval_max=0.001,
            max_progress_step=40,
        ),
    )


@pytest.fixture
async def bus() -> AsyncIterator[EventBus]:
    """事件总线（用完关掉）。"""
    instance = EventBus()
    yield instance
    await instance.close()


@pytest.fixture
async def env(
    uow: UnitOfWork, settings: Settings, bus: EventBus
) -> AsyncIterator[tuple[StudyService, SessionRegistry]]:
    """刷课服务 + 会话注册表（后者用于注入"已登录"状态）。"""
    transport = ChaoxingTransport(ChaoxingSettings(request_min_interval=0, max_retry=0))
    registry = SessionRegistry()
    service = StudyService(
        uow=uow,
        transport=transport,
        registry=registry,
        bus=bus,
        settings=settings,
        sleep=no_wait,
        rng=random.Random(0),
    )
    yield service, registry
    await transport.aclose()


def sign_in(registry: SessionRegistry, account_id: int) -> None:
    """给账号注入一个"已登录"的会话（测试不必真的走一遍登录）。"""
    state = registry.get(account_id)
    state.cookies["_uid"] = "100000005"
    state.status = "logged_in"


async def seed_video_task(
    uow: UnitOfWork, *, duration: float = 10.0, progress: float = 0.0
) -> tuple[int, int]:
    """造一条视频任务点（含卡片透传字段），返回 (account_id, task_point_id)。"""
    async with uow.begin() as session:
        account = Account(username="13800000000")
        session.add(account)
        await session.flush()
        course = Course(account_id=account.id, course_key="1:2", title="测试课")
        session.add(course)
        await session.flush()
        point = TaskPoint(
            account_id=account.id,
            course_id=course.id,
            point_id="obj-1",
            job_id="job-1",
            kind="video",
            title="1.1 函数",
            sequence=1001,
            duration=duration,
            progress=progress,
            payload={
                "clazzId": "151148348",
                "userid": "100000002",
                "courseId": "265361351",
                "cpi": "507072787",
                "otherInfo": "nodeId_1-cpi_507072787-enc_x&courseId=265361351",
                "rate": 2.0,
            },
        )
        session.add(point)
        await session.flush()
        return account.id, point.id


def mock_video_info(duration: float = 10.0) -> None:
    """复刻"打开播放器页 → 取视频元信息"这对接口。

    **播放器页不能少**：心跳要用播放器页访问后才签发的那个 `dtoken`，
    少了它服务端一律 403（真账号实测，见 docs/MOOC_ANS_403.md）。
    早先的测试只 mock 了 status，所以修复后它们会因"未 mock 的请求"而失败 ——
    那正是修复生效的证据。
    """
    respx.get(url__startswith=f"{MOOC1}/ananas/modules/video/index.html").mock(
        return_value=httpx.Response(200, text="<html>player</html>")
    )
    respx.get(f"{MOOC1}/ananas/status/obj-1").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "dtoken": "dt-1", "duration": duration}
        )
    )


def mock_heartbeat(*responses: dict[str, object]) -> None:
    """复刻心跳接口（按调用顺序返回给定回执，用完后重复最后一条）。"""
    route = respx.get(url__startswith=f"{MOOC1}/mooc-ans/multimedia/log/a/507072787/dt-1")
    if len(responses) == 1:
        route.mock(return_value=httpx.Response(200, json=responses[0]))
    else:
        route.mock(side_effect=[httpx.Response(200, json=item) for item in responses])


async def test_task_failure_writes_payload_and_traceback_to_account_log(
    uow: UnitOfWork,
    env: tuple[StudyService, SessionRegistry],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """任务失败时，按账号日志必须留下服务端原始返回与 traceback，而不只是泛化文案。"""
    study, registry = env
    account_id, _ = await seed_video_task(uow, duration=5.0)
    sign_in(registry, account_id)
    study._account_log = AccountLogWriter(tmp_path / "accounts")

    async def fail(_account_id: int, _point: PointView) -> str:
        raise EnvelopeFailure(
            "作业提交失败！",
            payload={"status": False, "msg": "作业提交失败！", "msg2": "作业提交失败！"},
        )

    monkeypatch.setattr(study, "_run_video", fail)

    report = await study.run_account(account_id)

    assert report.failed == 1
    lines = (tmp_path / "accounts" / f"{account_id}.jsonl").read_text(encoding="utf-8").splitlines()
    failed = next(json.loads(line) for line in lines if json.loads(line)["event"] == "task_failed")
    assert failed["error_type"] == "EnvelopeFailure"
    assert failed["payload"]["msg2"] == "作业提交失败！"
    assert "EnvelopeFailure" in failed["traceback"]
    assert any(json.loads(line)["event"] == "run_finished" for line in lines)


async def test_active_accounts_reports_running_session_and_current_point(
    uow: UnitOfWork, env: tuple[StudyService, SessionRegistry]
) -> None:
    """仪表盘数据源：running 的 study_sessions + 当前 running 任务点。"""
    study, registry = env
    account_id, point_id = await seed_video_task(uow, duration=10.0)
    sign_in(registry, account_id)
    async with uow.begin() as session:
        point = await session.get(TaskPoint, point_id)
        assert point is not None
        point.status = "running"
        point.progress = 3.0

    session_id = await study._start_session(account_id)
    runs = await study.active_accounts()

    assert len(runs) == 1
    assert runs[0].account_id == account_id
    assert runs[0].point_title == "1.1 函数"
    assert runs[0].progress == 3.0

    await study._finish_session(
        session_id, StudyReport(account_id=account_id, done=1), failed=False
    )
    async with uow.read() as session:
        rows = (await session.scalars(select(StudySession))).all()
        assert len(rows) == 1
        assert rows[0].status == "finished"
        assert rows[0].summary["done"] == 1
    assert await study.active_accounts() == []


@respx.mock
async def test_video_completes_and_never_pulls_stream(
    uow: UnitOfWork, env: tuple[StudyService, SessionRegistry]
) -> None:
    """心跳推到时长后服务端认可 → 完成；**全程不请求任何媒体流**。"""
    study, registry = env
    account_id, _ = await seed_video_task(uow, duration=10.0)
    sign_in(registry, account_id)
    mock_video_info(10.0)
    mock_heartbeat({"isPassed": True, "progress": 10})

    report = await study.run_account(account_id)

    assert report.done == 1
    assert report.failed == 0
    urls = [str(call.request.url) for call in respx.calls]
    assert not any(ext in url for url in urls for ext in (".m3u8", ".ts", ".mp4"))
    async with uow.read() as session:
        stored = (await session.scalars(select(TaskPoint))).one()
        assert stored.status == "done"


@respx.mock
async def test_two_accounts_run_concurrently_without_cookie_crosstalk(
    uow: UnitOfWork, env: tuple[StudyService, SessionRegistry]
) -> None:
    """两个账号并发刷课：各自 cookie 不串号，互不阻塞，各自出报告。

    这正是 ``AccountScheduler`` 的隔离承诺（来源：4C4G 多账号真机跑批，A 账号的
    心跳带 B 的 ``_uid`` 导致 401 的事故）。这里用真实 ``StudyService`` + 共享
    transport 跑一遍，而不是只信 fake。
    """
    from chaoxing.core.scheduler import AccountScheduler

    study, registry = env
    first_account, _ = await seed_video_task(uow, duration=5.0)
    second_account, _ = await seed_video_task(uow, duration=5.0)
    sign_in(registry, first_account)
    sign_in(registry, second_account)
    registry.get(first_account).cookies["_uid"] = "111111111"
    registry.get(second_account).cookies["_uid"] = "222222222"
    mock_video_info(5.0)
    mock_heartbeat({"isPassed": True, "progress": 5})

    scheduler = AccountScheduler(study=study, max_concurrency=2)
    result = await scheduler.run([first_account, second_account])

    assert result.ok is True
    assert result.errors == {}
    assert set(result.reports) == {first_account, second_account}
    assert all(report.done == 1 for report in result.reports.values())

    cookies = [call.request.headers.get("cookie", "") for call in respx.calls]
    assert any("_uid=111111111" in item for item in cookies)
    assert any("_uid=222222222" in item for item in cookies)
    assert not any("_uid=111111111" in item and "_uid=222222222" in item for item in cookies)


@respx.mock
async def test_video_without_server_acknowledgement_is_failed(
    uow: UnitOfWork, env: tuple[StudyService, SessionRegistry]
) -> None:
    """时间轴走完但服务端始终不认可 → 判失败，**绝不谎报完成**。"""
    study, registry = env
    account_id, _ = await seed_video_task(uow, duration=10.0)
    sign_in(registry, account_id)
    mock_video_info(10.0)
    mock_heartbeat({"isPassed": False})

    report = await study.run_account(account_id)

    assert report.done == 0
    assert report.failed == 1
    async with uow.read() as session:
        stored = (await session.scalars(select(TaskPoint))).one()
        assert stored.status == "failed"


@respx.mock
async def test_face_capture_marks_need_human(
    uow: UnitOfWork, env: tuple[StudyService, SessionRegistry]
) -> None:
    """回执里出现人脸抓拍标记 → 标记待人工，且不再推进。"""
    study, registry = env
    account_id, _ = await seed_video_task(uow, duration=10.0)
    sign_in(registry, account_id)
    mock_video_info(10.0)
    mock_heartbeat({"videoFaceCapture": 1})

    report = await study.run_account(account_id)

    assert report.blocked == 1
    async with uow.read() as session:
        stored = (await session.scalars(select(TaskPoint))).one()
        assert stored.status == "need_human"


@respx.mock
async def test_progress_events_are_emitted(
    uow: UnitOfWork, env: tuple[StudyService, SessionRegistry], bus: EventBus
) -> None:
    """进度事件会发出来（前端进度条与热力图靠它，且它不落库）。"""
    study, registry = env
    account_id, _ = await seed_video_task(uow, duration=100.0)
    sign_in(registry, account_id)
    mock_video_info(100.0)
    mock_heartbeat({"isPassed": False}, {"isPassed": True})
    progresses: list[TaskPointProgress] = []

    async def collect(event: object) -> None:
        if isinstance(event, TaskPointProgress):
            progresses.append(event)

    bus.subscribe(collect)

    report = await study.run_account(account_id)
    await bus.wait_idle(timeout=2.0)

    assert report.done == 1
    assert progresses, "进度事件没有发出来"
    assert progresses[-1].progress > 0


@respx.mock
async def test_missing_dtoken_fails_fast(
    uow: UnitOfWork, env: tuple[StudyService, SessionRegistry]
) -> None:
    """拿不到 dtoken 时明确失败，而不是空转心跳。"""
    study, registry = env
    account_id, _ = await seed_video_task(uow)
    sign_in(registry, account_id)
    # 播放器页与 status 都要 mock —— 前者是后者被调用的前提（心跳链路的第一步）
    respx.get(url__startswith=f"{MOOC1}/ananas/modules/video/index.html").mock(
        return_value=httpx.Response(200, text="<html>player</html>")
    )
    respx.get(f"{MOOC1}/ananas/status/obj-1").mock(
        return_value=httpx.Response(200, json={"status": "fail"})
    )

    report = await study.run_account(account_id)

    assert report.failed == 1


@respx.mock
async def test_without_login_state_fails_without_network(
    uow: UnitOfWork, env: tuple[StudyService, SessionRegistry]
) -> None:
    """没有登录态时明确失败，且**不发任何请求**。"""
    study, _ = env
    account_id, _ = await seed_video_task(uow)

    report = await study.run_account(account_id)

    assert report.failed == 1
    assert not respx.calls


# --------------------------------------------------------------------------- #
# 控制检查点（计划 Task P3）：暂停 / 继续 / 中止
# --------------------------------------------------------------------------- #
@respx.mock
async def test_paused_account_starts_no_unit(
    uow: UnitOfWork, env: tuple[StudyService, SessionRegistry]
) -> None:
    """上一轮已暂停在案：本次 run 一个任务点都不开，连视频信息都不取。"""
    study, registry = env
    account_id, _ = await seed_video_task(uow)
    sign_in(registry, account_id)
    await study.control.request(account_id, RUN_PAUSE_REQUESTED)
    await study.control.checkpoint(account_id)  # 上一轮在这里交出控制权 → paused

    report = await study.run_account(account_id)

    assert report.units == 1, "单元要照计划切出来，好回答还剩多少没跑"
    assert report.aborted == 1
    assert report.abort_reason == RUN_PAUSED
    assert report.done == 0 and report.failed == 0
    assert "已暂停" in report.details[0], "提前收尾的原因要如实写进报告"
    assert not respx.calls, "暂停中不该发起任何请求"
    async with uow.read() as session:
        stored = (await session.scalars(select(TaskPoint))).one()
        assert stored.status == "queued", "没跑过的任务点不该被改动"


@respx.mock
async def test_pause_at_heartbeat_keeps_progress_and_resumes(
    uow: UnitOfWork, env: tuple[StudyService, SessionRegistry]
) -> None:
    """心跳检查点命中暂停：不发这一拍心跳、进度不动；继续后从断点跑完。"""
    study, registry = env
    account_id, _ = await seed_video_task(uow, duration=100.0)
    sign_in(registry, account_id)
    mock_video_info(100.0)
    mock_heartbeat({"isPassed": True})

    armed = True

    async def pause_once(_seconds: float) -> None:
        # 模拟"用户在第一拍心跳之前点了暂停"
        nonlocal armed
        if armed:
            armed = False
            await study.control.request(account_id, RUN_PAUSE_REQUESTED)

    study._sleep = pause_once  # 测试要在服务内注入"暂停时机"

    first = await study.run_account(account_id)

    assert first.aborted == 1
    assert first.abort_reason == RUN_PAUSED
    assert first.done == 0
    urls = [str(call.request.url) for call in respx.calls]
    assert not any("/multimedia/log/" in url for url in urls), "暂停后不该再推心跳"
    async with uow.read() as session:
        stored = (await session.scalars(select(TaskPoint))).one()
        assert stored.status == "running", "中断的视频要留在 running，下次才知道从哪继续"
        assert stored.progress == 0.0, "没成功上报的进度不能凭空前进"

    # 继续：信号被下一个检查点消费，任务从断点跑完
    await study.control.request(account_id, RUN_RESUME_REQUESTED)
    second = await study.run_account(account_id)

    assert second.aborted == 0
    assert second.done == 1
    async with uow.read() as session:
        stored = (await session.scalars(select(TaskPoint))).one()
        assert stored.status == "done"


@respx.mock
async def test_stop_at_heartbeat_finishes_early_and_consumes_signal(
    uow: UnitOfWork, env: tuple[StudyService, SessionRegistry]
) -> None:
    """心跳检查点命中中止：提前收尾、原因如实、控制行被消费掉。"""
    study, registry = env
    account_id, _ = await seed_video_task(uow, duration=100.0)
    sign_in(registry, account_id)
    mock_video_info(100.0)
    mock_heartbeat({"isPassed": True})

    async def stop_once(_seconds: float) -> None:
        await study.control.request(account_id, RUN_STOP_REQUESTED)

    study._sleep = stop_once

    report = await study.run_account(account_id)

    assert report.aborted == 1
    assert report.abort_reason == RUN_STOP_REQUESTED
    assert report.abort_reason in report.details[0]
    current = await study.control.current(account_id)
    assert current.state == RUN_RUNNING, "中止请求被消费后不该残留控制行"


@respx.mock
async def test_failed_point_at_retry_limit_is_not_reselected(
    env: tuple[StudyService, SessionRegistry], uow: UnitOfWork
) -> None:
    """失败点只再试一次：`retry_count` 到 `RETRY_LIMIT` 后不再被自动选中。

    真机实测（2026-10-12）：15 个被 403 拦下的测验每次 run 都被重选，retry 涨到 3 仍失败，
    全是白耗请求。要重试得走 `cx study reset`（那条路会把 retry_count 清零）。
    """
    service, registry = env
    account_id, point_id = await seed_video_task(uow)
    sign_in(registry, account_id)
    async with uow.begin() as session:
        row = await session.get(TaskPoint, point_id)
        assert row is not None
        row.status = "failed"
        row.retry_count = RETRY_LIMIT

    mock_video_info()
    report = await service.run_account(account_id, course_ids=None)

    assert report.units == 0, "到上限的失败点不该被选中"
    assert not respx.calls, "一个请求都不该发出去"


@respx.mock
async def test_failed_point_below_retry_limit_is_still_retried(
    env: tuple[StudyService, SessionRegistry], uow: UnitOfWork
) -> None:
    """边界另一侧：还没到上限就仍然被选中（否则"不再重试"会退化成"永不重试"）。

    直接断言**选择**这一步（本轮改的就是它），不去真跑视频 —— 那样还得铺心跳接口的 mock。
    """
    service, registry = env
    account_id, point_id = await seed_video_task(uow)
    sign_in(registry, account_id)
    async with uow.begin() as session:
        row = await session.get(TaskPoint, point_id)
        assert row is not None
        row.status = "failed"
        row.retry_count = RETRY_LIMIT - 1

    selected = await service._load_points(account_id, None)

    assert [point.id for point in selected] == [point_id]


async def seed_read_document(
    uow: UnitOfWork, *, enc: str = "71fe15dbec62105f7667c6617c7881a5"
) -> tuple[int, int]:
    """造一条「阅读 / 专题阅读」任务点（``jobid`` 形如 ``read-<数字>``，payload 带 ``enc``）。"""
    async with uow.begin() as session:
        account = Account(username="13800000002")
        session.add(account)
        await session.flush()
        course = Course(account_id=account.id, course_key="3:4", title="阅读课")
        session.add(course)
        await session.flush()
        point = TaskPoint(
            account_id=account.id,
            course_id=course.id,
            point_id="read-212697622",
            job_id="read-212697622",
            kind="document",
            title="文学与人生",
            sequence=1,
            payload={
                "courseId": "266376529",
                "clazzId": "154336713",
                "cpi": "581371075",
                "enc": enc,
                "knowledgeid": "1228327964",
            },
        )
        session.add(point)
        await session.flush()
        return account.id, point.id


@respx.mock
async def test_read_job_uses_readv2_show(
    env: tuple[StudyService, SessionRegistry], uow: UnitOfWork
) -> None:
    """``read-`` 型阅读卡走**真机实测**的完成上报，而不是在真机一律被拒的经典文档接口。

    2026-10-12 抓包：浏览器打开阅读卡时调 ``/mooc-ans/coursedata/readjobv2/show``（带附件里的 ``enc``），
    调用后平台侧进度立刻变已完成（`sync_progress` 复核）；经典 ``/ananas/job/{document,readv2}``
    对这类卡只回「无效的请求参数」。
    """
    service, registry = env
    account_id, _point_id = await seed_read_document(uow)
    sign_in(registry, account_id)
    show = respx.get(
        url__startswith="https://mooc1.chaoxing.com/mooc-ans/coursedata/readjobv2/show"
    ).mock(return_value=httpx.Response(200, text="<html>专题阅读任务点</html>"))
    classic = respx.get(url__startswith="https://mooc1.chaoxing.com/ananas/job/document").mock(
        return_value=httpx.Response(200, json={"status": True, "msg": "ok"})
    )

    report = await service.run_account(account_id, course_ids=None)

    assert report.done == 1
    assert show.called, "阅读卡必须走 readv2 上报"
    assert not classic.called, "不该再撞经典文档接口"
    params = dict(show.calls[0].request.url.params)
    assert params["djobid"] == "read-212697622"
    assert params["enc"] == "71fe15dbec62105f7667c6617c7881a5"
    assert params["clazzId"] == "154336713"
    assert params["cpi"] == "581371075"


@respx.mock
async def test_read_job_with_numeric_jobid_also_uses_readv2(
    env: tuple[StudyService, SessionRegistry], uow: UnitOfWork
) -> None:
    """**判据是"卡片带 enc"，不是 jobid 前缀**（真机数据里两种都有）。

    真机教训（2026-10-12）：只有部分阅读卡的 jobid 形如 ``read-<数字>``，
    `#178/#180/#181` 是纯数字 id —— 按前缀路由会把这三张卡漏到经典接口上，
    真机表现就是「无效的请求参数」。
    """
    service, registry = env
    async with uow.begin() as session:
        account = Account(username="13800000003")
        session.add(account)
        await session.flush()
        course = Course(account_id=account.id, course_key="5:6", title="阅读课2")
        session.add(course)
        await session.flush()
        point = TaskPoint(
            account_id=account.id,
            course_id=course.id,
            point_id="bb3620a98ecebe3e0a5839156fead560",
            job_id="1596088442818632",
            kind="document",
            title="第四章 大学生仪态礼仪.docx",
            sequence=1,
            payload={
                "courseId": "266376529",
                "clazzId": "154336713",
                "cpi": "581371075",
                "enc": "deadbeefdeadbeefdeadbeefdeadbeef",
                "knowledgeid": "1228327964",
            },
        )
        session.add(point)
        await session.flush()
        account_id = account.id

    sign_in(registry, account_id)
    show = respx.get(
        url__startswith="https://mooc1.chaoxing.com/mooc-ans/coursedata/readjobv2/show"
    ).mock(return_value=httpx.Response(200, text="<html>专题阅读任务点</html>"))

    report = await service.run_account(account_id, course_ids=None)

    assert report.done == 1
    assert show.called
    assert dict(show.calls[0].request.url.params)["djobid"] == "1596088442818632"


class _StubCourses:
    """课程服务替身：只回放一个固定刷新结果，并记录调用入参（验证合并逻辑用）。"""

    def __init__(self, fresh: dict[str, str]) -> None:
        self.fresh = fresh
        self.calls: list[dict[str, object]] = []

    async def refresh_quiz_tokens(self, account_id: int, **kwargs: object) -> dict[str, str]:
        """回放预设结果。"""
        self.calls.append({"account_id": account_id, **kwargs})
        return self.fresh


class _StubProgressCourses:
    """课程服务替身：只回放本次目录页的 done_chapters。"""

    def __init__(self, done: set[str]) -> None:
        """记录本次完成的章节 ID。"""
        self.done = done
        self.calls = 0

    async def sync_progress(self, account_id: int, course_key: str) -> ProgressSyncReport:
        """回放固定进度报告。"""
        self.calls += 1
        return ProgressSyncReport(
            account_id=account_id,
            course_key=course_key,
            chapters=1,
            remote_done=len(self.done),
            done_chapters=set(self.done),
        )


async def test_document_progress_uses_fresh_chapter_map(
    uow: UnitOfWork, settings: Settings, bus: EventBus
) -> None:
    """文档完成复核只看**本次**目录页，不拿旧的 done 行倒推。"""
    courses = _StubProgressCourses({"ch-1"})
    point = PointView(
        id=1,
        point_id="read-1",
        kind="document",
        chapter_id="ch-1",
        payload={"courseId": "1", "clazzId": "2"},
    )
    async with _service_with_courses(uow, settings, bus, courses) as service:
        assert await service._document_progress_done(1, point) is True

        courses.done = set()
        assert await service._document_progress_done(1, point) is False
    assert courses.calls == 2


def _quiz_point() -> PointView:
    """造一个测验任务点视图（payload 是扫描时存下的"旧票据"）。"""
    return PointView(
        id=1,
        point_id="p-quiz",
        job_id="job-quiz",
        kind="quiz",
        chapter_id="1192366344",
        payload={
            "courseId": "265361351",
            "clazzId": "151148348",
            "cpi": "507072787",
            "workid": "0f9c1e2d3a4b5c6d7e8f90a1b2c3d4e5",
            "ktoken": "old-ktoken",
            "enc": "old-enc",
            "userid": "100000002",
        },
    )


@asynccontextmanager
async def _service_with_courses(
    uow: UnitOfWork, settings: Settings, bus: EventBus, courses: object
) -> AsyncIterator[StudyService]:
    """建一个注入了课程服务替身的刷课服务（用完关掉传输层）。"""
    transport = ChaoxingTransport(ChaoxingSettings(request_min_interval=0, max_retry=0))
    try:
        yield StudyService(
            uow=uow,
            transport=transport,
            registry=SessionRegistry(),
            bus=bus,
            settings=settings,
            sleep=no_wait,
            courses=courses,  # type: ignore[arg-type]
        )
    finally:
        await transport.aclose()


async def test_quiz_payload_merge_overrides_fresh_and_keeps_old(
    uow: UnitOfWork, settings: Settings, bus: EventBus
) -> None:
    """刷新结果**覆盖**同名字段，旧字段**不丢** —— 换票/提交都用合并后的 payload。"""
    stub = _StubCourses({"enc": "new-enc", "utEnc": "new-utenc"})
    point = _quiz_point()

    async with _service_with_courses(uow, settings, bus, stub) as service:
        merged = await service._refresh_quiz_payload(7, point)

    assert merged["enc"] == "new-enc", "同名字段必须被新票据覆盖"
    assert merged["utEnc"] == "new-utenc", "新字段要补进来"
    assert merged["ktoken"] == "old-ktoken", "旧字段不能丢"
    assert merged["userid"] == "100000002"
    assert merged["workid"] == "0f9c1e2d3a4b5c6d7e8f90a1b2c3d4e5"
    # 入参取自 payload，供课程服务按章节/作业重扫
    assert stub.calls[0] == {
        "account_id": 7,
        "course_id": "265361351",
        "clazz_id": "151148348",
        "chapter_id": "1192366344",
        "work_id": "0f9c1e2d3a4b5c6d7e8f90a1b2c3d4e5",
    }


async def test_quiz_payload_keeps_original_when_refresh_empty(
    uow: UnitOfWork, settings: Settings, bus: EventBus
) -> None:
    """刷新拿不到新票据时原样返回旧 payload（失败要软，不许让任务点直接失败）。"""
    stub = _StubCourses({})
    point = _quiz_point()

    async with _service_with_courses(uow, settings, bus, stub) as service:
        merged = await service._refresh_quiz_payload(7, point)

    assert merged == point.payload


async def test_quiz_payload_without_courses_service_is_untouched(
    uow: UnitOfWork, settings: Settings, bus: EventBus
) -> None:
    """未注入课程服务时退化为"不刷新"，payload 原样返回（不影响其它链路）。"""
    point = _quiz_point()

    async with _service_with_courses(uow, settings, bus, None) as service:
        merged = await service._refresh_quiz_payload(7, point)

    assert merged == point.payload


async def test_open_quiz_page_refreshes_tokens_and_retries_once(
    uow: UnitOfWork, settings: Settings, bus: EventBus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """换票 403 后必须**重新扫卡**再换一次，而不是拿同一组旧票据重放。

    真机根因是 ``ktoken`` / ``enc`` 过期；重放旧票据只会再次 403。
    """
    from chaoxing.client.exceptions import RequestBlocked

    point = _quiz_point()
    calls = {"redirect": 0, "refresh": 0}

    async with _service_with_courses(uow, settings, bus, None) as service:
        session = service._registry.get(7)

        async def redirect(session: object, target: PointView, work_id: str) -> str:
            calls["redirect"] += 1
            if calls["redirect"] == 1:
                raise RequestBlocked("HTTP 403")
            return f"{MOOC1}/mooc-ans/work/doHomeWorkNew?workId={work_id}&enc=fresh"

        async def fetch(session: object, *, work_id: str, url: str) -> str:
            return "<html>题目页</html>"

        async def refresh(account_id: int, target: PointView) -> dict[str, object]:
            calls["refresh"] += 1
            return {**target.payload, "ktoken": "new-ktoken"}

        monkeypatch.setattr(service, "_quiz_redirect", redirect)
        monkeypatch.setattr(service._quiz_api, "fetch_detail_html", fetch)
        monkeypatch.setattr(service, "_refresh_quiz_payload", refresh)
        final_url, html, payload = await service._open_quiz_page(
            7, session, point, "work-1", point.payload
        )

    assert calls == {"redirect": 2, "refresh": 1}
    assert "doHomeWorkNew" in final_url
    assert html == "<html>题目页</html>"
    assert payload["ktoken"] == "new-ktoken"


def test_review_page_counts_as_work_url() -> None:
    """已交卷的测验换票会落到批阅页（selectWorkQuestionYiPiYue），必须仍算「落到作业页」。

    真机 2026-10-13：用户手动交卷后 #33 的换票落点就是它；若判成"换票失败"，
    其实已完成的点会被记成 failed。用工具自己的路径复跑时 #10/#33 都正确地走了
    `already_done`。
    """
    review = (
        "https://mooc1.chaoxing.com/mooc-ans/work/selectWorkQuestionYiPiYue"
        "?courseId=235810276&workId=55605659&workAnswerId=55414596&oldWorkId=7b3e5042"
    )

    assert _looks_like_work_url(review) is True
    assert _looks_like_work_url("https://mooc1.chaoxing.com/mooc-ans/x/doHomeWorkNew?a=1") is True
    assert _looks_like_work_url("https://mooc1.chaoxing.com/passport/login") is False


# --------------------------------------------------------------------------- #
# 逐题型提交格式（连线题走 JSON 映射，其余题型零变化）
# --------------------------------------------------------------------------- #

#: 连线题的真实片段（与 ``tests/client/test_parser_quiz.py`` 共用）
_MATCHING_HTML = (
    Path(__file__).resolve().parents[1] / "fixtures" / "quiz_matching.html"
).read_text(encoding="utf-8")

#: 四道题（单选 / 多选 / 连线 / 判断），顺序与题目页 ID 一一对应
_PLAIN_HTML = (
    '<div class="TiMu"><div class="Zy_TItle_p">'
    "<p>【单选题】格雷的强化敏感性理论属于哪一类？</p></div>"
    '<ul class="Zy_ulTop"><li><i>A、</i><a>动机系统</a></li>'
    "<li><i>B、</i><a>神经系统</a></li></ul></div>"
    '<div class="TiMu"><div class="Zy_TItle_p">'
    "<p>【多选题】奥尔波特认为研究人格的基本策略包括？</p></div>"
    '<ul class="Zy_ulTop"><li><i>A、</i><a>因素分析法</a></li>'
    "<li><i>B、</i><a>相关分析法</a></li></ul></div>"
    + _MATCHING_HTML
    + '<div class="TiMu"><div class="Zy_TItle_p">'
    "<p>【判断题】神经质是最稳定、最有预测力的因子。</p></div></div>"
)

_DETAIL_HTML = (
    '<input type="hidden" name="courseId" value="235810276">'
    '<div id="question403812141"></div>'
    '<div id="question403812140"></div>'
    '<div id="question403812142"></div>'
    '<div id="question403812139"></div>'
)

_WORK_URL = f"{MOOC1}/mooc-ans/work/doHomeWorkNew?workId=55611861&enc=abc"


class _StubAnswers:
    """按题型给固定答案的假 provider（连线题给「序号=字母」）。"""

    async def answer(self, question: Question, *, snippets: object = None) -> Answer:
        """回放一个稳定答案。"""
        value = {
            "单选题": "A",
            "多选题": "AB",
            "判断题": "对",
            "连线题": "1=D\n2=A",
        }.get(question.qtype_hint, "A")
        return Answer(fingerprint=question.fingerprint, value=value, source="model", confidence=0.9)


class _MissingOneAnswers(_StubAnswers):
    """少答一道判断题 —— 用来验证完整性闸门。"""

    async def answer(self, question: Question, *, snippets: object = None) -> Answer:
        """判断题回空答案，其余正常。"""
        if "判断题" in question.qtype_hint:
            return Answer(fingerprint=question.fingerprint, value="", source="none")
        return await super().answer(question, snippets=snippets)


@respx.mock
async def test_quiz_incomplete_never_submits(
    uow: UnitOfWork, settings: Settings, bus: EventBus
) -> None:
    """缺一题就不交卷：服务端接受部分答案但记 0 分，任务点却会变完成。"""
    transport = ChaoxingTransport(ChaoxingSettings(request_min_interval=0, max_retry=0))
    registry = SessionRegistry()
    service = StudyService(
        uow=uow,
        transport=transport,
        registry=registry,
        bus=bus,
        settings=settings,
        sleep=no_wait,
        answers=AnswerEngine(uow=uow, provider=_MissingOneAnswers()),  # type: ignore[arg-type]
    )
    try:
        async with uow.begin() as session:
            account = Account(username="13800000009")
            session.add(account)
            await session.flush()
            course = Course(account_id=account.id, course_key="7:8", title="人格课")
            session.add(course)
            await session.flush()
            session.add(
                TaskPoint(
                    account_id=account.id,
                    course_id=course.id,
                    point_id="p-quiz",
                    job_id="job-quiz",
                    kind="quiz",
                    title="第二章 课后作业",
                    sequence=1,
                    payload={
                        "courseId": "235810276",
                        "clazzId": "154448099",
                        "cpi": "581371075",
                        "workid": "353f330c1147428d99d86f416e987b62",
                        "userid": "471652995",
                        "ktoken": "k",
                    },
                )
            )
            await session.flush()
            account_id = account.id

        sign_in(registry, account_id)
        respx.get(f"{MOOC1}/mooc-ans/api/work").mock(
            return_value=httpx.Response(302, headers={"location": _WORK_URL})
        )
        respx.get(_WORK_URL).mock(return_value=httpx.Response(200, text=_DETAIL_HTML))
        respx.get(url__startswith=f"{MOOC1}/mooc-ans/api/selectWorkQuestion").mock(
            return_value=httpx.Response(200, text=_PLAIN_HTML)
        )
        submit = respx.post(url__startswith=f"{MOOC1}/mooc-ans/work/addStudentWorkNewWeb").mock(
            return_value=httpx.Response(200, json={"status": True, "msg": "success!"})
        )

        report = await service.run_account(account_id)

        assert report.failed == 1
        assert not submit.called
    finally:
        await transport.aclose()


@respx.mock
async def test_quiz_submits_matching_as_json_and_others_unchanged(
    uow: UnitOfWork, settings: Settings, bus: EventBus
) -> None:
    """连线题提交 ``[{"name":..,"content":..}]`` JSON；单选 / 多选 / 判断题格式**零变化**。"""
    transport = ChaoxingTransport(ChaoxingSettings(request_min_interval=0, max_retry=0))
    registry = SessionRegistry()
    service = StudyService(
        uow=uow,
        transport=transport,
        registry=registry,
        bus=bus,
        settings=settings,
        sleep=no_wait,
        answers=AnswerEngine(uow=uow, provider=_StubAnswers()),  # type: ignore[arg-type]
    )
    try:
        async with uow.begin() as session:
            account = Account(username="13800000009")
            session.add(account)
            await session.flush()
            course = Course(account_id=account.id, course_key="7:8", title="人格课")
            session.add(course)
            await session.flush()
            session.add(
                TaskPoint(
                    account_id=account.id,
                    course_id=course.id,
                    point_id="p-quiz",
                    job_id="job-quiz",
                    kind="quiz",
                    title="第二章 课后作业",
                    sequence=1,
                    payload={
                        "courseId": "235810276",
                        "clazzId": "154448099",
                        "cpi": "581371075",
                        "workid": "353f330c1147428d99d86f416e987b62",
                        "userid": "471652995",
                        "ktoken": "k",
                    },
                )
            )
            await session.flush()
            account_id = account.id

        sign_in(registry, account_id)
        respx.get(f"{MOOC1}/mooc-ans/api/work").mock(
            return_value=httpx.Response(302, headers={"location": _WORK_URL})
        )
        respx.get(_WORK_URL).mock(return_value=httpx.Response(200, text=_DETAIL_HTML))
        respx.get(url__startswith=f"{MOOC1}/mooc-ans/api/selectWorkQuestion").mock(
            return_value=httpx.Response(200, text=_PLAIN_HTML)
        )
        # 交卷前的校验码预检（真机 2026-10-16）：拿到的 enc 就是 body 的 workTimesEnc
        respx.get(f"{MOOC1}/mooc-ans/work/verify/code").mock(
            return_value=httpx.Response(200, json={"enc": "e0bfc5fb", "status": True})
        )
        submit = respx.post(url__startswith=f"{MOOC1}/mooc-ans/work/addStudentWorkNewWeb").mock(
            return_value=httpx.Response(200, json={"status": True, "msg": "success!"})
        )

        report = await service.run_account(account_id)

        assert report.done == 1
        form = parse_qs(submit.calls[0].request.content.decode())
        # 连线题：JSON 数组（name=左项序号，content=右项字母）
        assert form["answer403812142"] == [
            '[{"name":"1","content":"D"},{"name":"2","content":"A"}]'
        ]
        # 其余题型格式零变化
        assert form["answer403812141"] == ["A"]
        assert form["answer403812140"] == ["AB"]
        assert form["answer403812139"] == ["true"]
    finally:
        await transport.aclose()
