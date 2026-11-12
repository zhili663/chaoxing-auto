"""刷课控制面：状态机、检查点、进度重置与事件回看。

来源：计划 Task P3。三条要钉住的性质：

* 信号在**数据库**里流转（CLI 与 API 是两个进程），非法转移直接报错而不是猜；
* ``paused`` 是"运行器已交出控制权"的**终点态**，检查点不阻塞等；
* ``reset`` 只把任务点打回未开始，**不删课程 / 账号 / 记录**。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chaoxing.core.services.study_control import (
    CHECK_CONTINUE,
    CHECK_PAUSE,
    CHECK_STOP,
    InvalidTransition,
    StudyControlService,
)
from chaoxing.storage.base import utcnow
from chaoxing.storage.models import (
    RUN_PAUSE_REQUESTED,
    RUN_PAUSED,
    RUN_RESUME_REQUESTED,
    RUN_RUNNING,
    RUN_STOP_REQUESTED,
    Account,
    Course,
    StudyRun,
    TaskEvent,
    TaskPoint,
)
from chaoxing.storage.uow import UnitOfWork


@pytest.fixture
def control(uow: UnitOfWork) -> StudyControlService:
    """跑在临时库上的控制面服务。"""
    return StudyControlService(uow=uow)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite 的 DATETIME 不带时区，读回来补上 UTC 再比较（写侧是带时区的）。"""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


async def seed_points(uow: UnitOfWork, account_id: int) -> tuple[int, int]:
    """造两门课 + 三个任务点（含已完成、失败两类），返回 ``(course_id, other_id)``。"""
    async with uow.begin() as db:
        course = Course(account_id=account_id, course_key="1:2", title="课程 A")
        other = Course(account_id=account_id, course_key="3:4", title="课程 B")
        db.add_all([course, other])
        await db.flush()
        db.add_all(
            [
                TaskPoint(
                    account_id=account_id,
                    course_id=course.id,
                    point_id="p1",
                    kind="video",
                    status="done",
                    progress=600.0,
                    duration=600.0,
                    sequence=1001,
                ),
                TaskPoint(
                    account_id=account_id,
                    course_id=course.id,
                    point_id="p2",
                    kind="quiz",
                    status="failed",
                    last_error="403",
                    retry_count=3,
                    sequence=1002,
                ),
                TaskPoint(
                    account_id=account_id,
                    course_id=other.id,
                    point_id="p3",
                    kind="video",
                    status="done",
                    progress=30.0,
                    duration=30.0,
                    sequence=2001,
                ),
            ]
        )
        await db.flush()
        return course.id, other.id


async def test_pause_resume_roundtrip(
    control: StudyControlService, account_id: int, session: AsyncSession
) -> None:
    """pause → pause_requested → 检查点 → paused → resume → 检查点 → running。"""
    assert (await control.current(account_id)).state == RUN_RUNNING

    requested = await control.request(account_id, RUN_PAUSE_REQUESTED)
    assert requested.state == RUN_PAUSE_REQUESTED
    assert requested.requested_at is not None, "请求时间要记下来"
    assert requested.note, "每个状态都要有中文说明"

    verdict = await control.checkpoint(account_id)
    assert (verdict.action, verdict.state) == (CHECK_PAUSE, RUN_PAUSED)
    assert (await control.current(account_id)).state == RUN_PAUSED

    # 已经暂停：再检查点还是"暂停"，不会自己变成"继续"
    assert (await control.checkpoint(account_id)).action == CHECK_PAUSE

    resumed = await control.request(account_id, RUN_RESUME_REQUESTED)
    assert resumed.state == RUN_RESUME_REQUESTED

    verdict = await control.checkpoint(account_id)
    assert verdict.action == CHECK_CONTINUE
    assert (await control.current(account_id)).state == RUN_RUNNING
    assert (await session.scalars(select(StudyRun))).all() == [], "继续请求被消费后不该留控制行"


async def test_checkpoint_without_signal_continues(
    control: StudyControlService, account_id: int
) -> None:
    """没有控制行就是"运行中"，检查点放行。"""
    verdict = await control.checkpoint(account_id)

    assert (verdict.action, verdict.state) == (CHECK_CONTINUE, RUN_RUNNING)


async def test_stop_is_consumed_once(
    control: StudyControlService, account_id: int, session: AsyncSession
) -> None:
    """stop_requested 在检查点被消费：收尾并清行（否则下一轮 run 立刻又退出）。"""
    await control.request(account_id, RUN_STOP_REQUESTED)

    verdict = await control.checkpoint(account_id)

    assert (verdict.action, verdict.state) == (CHECK_STOP, RUN_STOP_REQUESTED)
    assert (await session.scalars(select(StudyRun))).all() == []
    assert (await control.current(account_id)).state == RUN_RUNNING


async def test_idempotent_pause_keeps_request_time(
    control: StudyControlService, account_id: int
) -> None:
    """连点两次暂停不该报错，也不该刷新请求时间。"""
    first = await control.request(account_id, RUN_PAUSE_REQUESTED)
    second = await control.request(account_id, RUN_PAUSE_REQUESTED)

    assert second.state == RUN_PAUSE_REQUESTED
    assert as_utc(second.requested_at) == as_utc(first.requested_at), "幂等请求不该刷新时间戳"


async def test_resume_cancels_pending_pause(
    control: StudyControlService, account_id: int, session: AsyncSession
) -> None:
    """暂停还没被检查点消费时按继续 = 撤销该请求（回到运行中）。"""
    await control.request(account_id, RUN_PAUSE_REQUESTED)

    view = await control.request(account_id, RUN_RESUME_REQUESTED)

    assert view.state == RUN_RUNNING
    assert "取消" in view.note, "撤销暂停要说明白，不能只报一句运行中"
    assert (await session.scalars(select(StudyRun))).all() == []


async def test_illegal_transitions(control: StudyControlService, account_id: int) -> None:
    """非法转移一律报错（不静默改状态）。"""
    with pytest.raises(InvalidTransition):
        await control.request(account_id, RUN_RESUME_REQUESTED)  # 没有暂停却要继续
    with pytest.raises(InvalidTransition):
        await control.request(account_id, RUN_RUNNING)  # "运行中"不是可请求的状态
    with pytest.raises(InvalidTransition):
        await control.request(account_id, "whatever")

    await control.request(account_id, RUN_STOP_REQUESTED)
    with pytest.raises(InvalidTransition):
        await control.request(account_id, RUN_PAUSE_REQUESTED)  # 中止请求还没被消费

    await control.clear(account_id)
    await control.request(account_id, RUN_PAUSE_REQUESTED)
    await control.checkpoint(account_id)  # → paused
    with pytest.raises(InvalidTransition):
        await control.request(account_id, RUN_STOP_REQUESTED)  # 已经停了，没有在跑的东西可中止
    # 但"已暂停时再点暂停"是幂等的（前端连点、重试都可能发生）
    assert (await control.request(account_id, RUN_PAUSE_REQUESTED)).state == RUN_PAUSED


async def test_reset_requeues_points_and_keeps_data(
    control: StudyControlService,
    uow: UnitOfWork,
    account_id: int,
    session: AsyncSession,
) -> None:
    """reset 把任务点打回未开始（进度/错误/重试清零），**不删任何数据**。"""
    await seed_points(uow, account_id)
    await control.request(account_id, RUN_PAUSE_REQUESTED)
    await control.checkpoint(account_id)  # 留一个 paused 控制行

    count = await control.reset_progress(account_id)

    assert count == 3
    points = (await session.scalars(select(TaskPoint).order_by(TaskPoint.id))).all()
    assert [item.status for item in points] == ["queued"] * 3
    assert all(item.progress == 0.0 for item in points), "进度不清零的话下次 run 会假刷完"
    assert all(item.last_error == "" and item.retry_count == 0 for item in points)
    # 课程 / 账号 / 任务点都还在（reset 不是删除）
    assert len((await session.scalars(select(Course))).all()) == 2
    assert len((await session.scalars(select(Account))).all()) == 1
    # 控制信号也清掉：残留 paused 会让下一次 run 在第一个检查点就退出
    assert (await control.current(account_id)).state == RUN_RUNNING


async def test_reset_can_be_scoped_to_one_course(
    control: StudyControlService, uow: UnitOfWork, account_id: int, session: AsyncSession
) -> None:
    """``--course`` 只打回那门课的任务点。"""
    course_id, other_id = await seed_points(uow, account_id)

    count = await control.reset_progress(account_id, course_id=course_id)

    assert count == 2
    untouched = (
        await session.scalars(select(TaskPoint).where(TaskPoint.course_id == other_id))
    ).one()
    assert untouched.status == "done"
    assert untouched.progress == 30.0


async def test_recent_events_are_newest_first_and_limited(
    control: StudyControlService, uow: UnitOfWork, account_id: int
) -> None:
    """事件流水倒序、可按课程过滤、条数有上限。"""
    course_id, other_id = await seed_points(uow, account_id)
    base = utcnow()
    async with uow.begin() as db:
        for index in range(5):
            db.add(
                TaskEvent(
                    account_id=account_id,
                    course_id=course_id,
                    kind="video",
                    event="finished",
                    message=f"e{index}",
                    created_at=base + timedelta(seconds=index),
                )
            )
        db.add(
            TaskEvent(
                account_id=account_id,
                course_id=other_id,
                kind="quiz",
                event="quiz",
                message="other",
                created_at=base - timedelta(seconds=1),
            )
        )

    rows = await control.recent_events(account_id, limit=3)

    assert [item.message for item in rows] == ["e4", "e3", "e2"]
    assert len(await control.recent_events(account_id, limit=0)) == 1, "limit 下限是 1"
    assert len(await control.recent_events(account_id, limit=999)) == 6
    scoped = await control.recent_events(account_id, course_id=other_id)
    assert [item.message for item in scoped] == ["other"]
