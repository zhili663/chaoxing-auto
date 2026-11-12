"""统计：原子累加、按天补齐、KPI 口径。

来源：两个真实坑 ——
① "先查再改"的累加在多账号并发时会丢更新（读到旧值、都加一、写回同一个数）；
② 统计是给人看的，"今天刷了多少"必须跟本地日历一致，而不是 UTC 的日期切换。

另外：统计只能记在**真实存在**的账号上（外键拦着），所以每个用例都先建账号。
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from chaoxing.core.observers import ActivityObserver
from chaoxing.core.services.stats import StatsService, local_day
from chaoxing.obs.events import TaskPointFinished, TaskPointProgress
from chaoxing.storage.models import Account, ActivityDaily, Course, TaskPoint
from chaoxing.storage.uow import UnitOfWork


def yesterday() -> str:
    """昨天的本地日期（用于测"跨天不串"）。"""
    return local_day(date.today() - timedelta(days=1))


async def make_account(uow: UnitOfWork, username: str = "13800000000") -> int:
    """造一个账号并返回 ID。"""
    async with uow.begin() as session:
        account = Account(username=username)
        session.add(account)
        await session.flush()
        return account.id


async def test_bump_accumulates_instead_of_overwriting(uow: UnitOfWork) -> None:
    """同一天多次累加要**相加**（并发安全的语义要求）。"""
    account_id = await make_account(uow)
    stats = StatsService(uow=uow)

    await stats.bump(account_id, points=2, watched_seconds=60)
    await stats.bump(account_id, points=3, quizzes=1, watched_seconds=120)

    async with uow.read() as session:
        row = (await session.scalars(select(ActivityDaily))).one()
    assert row.points_done == 5
    assert row.quiz_done == 1
    assert row.watched_seconds == 180.0


async def test_bump_is_scoped_per_account_and_day(uow: UnitOfWork) -> None:
    """不同账号、不同日期互不干扰。"""
    first = await make_account(uow, "13800000001")
    second = await make_account(uow, "13800000002")
    stats = StatsService(uow=uow)

    await stats.bump(first, points=1)
    await stats.bump(second, points=5)
    await stats.bump(first, points=9, day=yesterday())

    async with uow.read() as session:
        rows = list((await session.scalars(select(ActivityDaily))).all())
    assert len(rows) == 3
    assert {row.points_done for row in rows} == {1, 5, 9}


async def test_daily_fills_missing_days(uow: UnitOfWork) -> None:
    """没活动的日期要补 0（前端不用自己补，图表也不会断）。"""
    account_id = await make_account(uow)
    stats = StatsService(uow=uow)
    await stats.bump(account_id, points=3)

    series = await stats.daily(account_id=account_id, days=7)

    assert len(series) == 7
    assert series[-1].day == local_day()
    assert series[-1].points_done == 3
    assert series[0].points_done == 0


async def test_daily_aggregates_all_accounts_when_asked(uow: UnitOfWork) -> None:
    """不指定账号时按天合计。"""
    first = await make_account(uow, "13800000001")
    second = await make_account(uow, "13800000002")
    stats = StatsService(uow=uow)
    await stats.bump(first, points=2)
    await stats.bump(second, points=3)

    series = await stats.daily(days=1)

    assert series[-1].points_done == 5


async def test_observer_counts_video_and_quiz_separately(uow: UnitOfWork) -> None:
    """观看时长只认视频；测验只计次数（否则 KPI 会被答题时间灌水）。"""
    account_id = await make_account(uow)
    observer = ActivityObserver(StatsService(uow=uow))

    await observer.handle(
        TaskPointFinished(account_id=account_id, kind="video", status="done", elapsed_s=90.0)
    )
    await observer.handle(
        TaskPointFinished(account_id=account_id, kind="quiz", status="done", elapsed_s=30.0)
    )

    async with uow.read() as session:
        row = (await session.scalars(select(ActivityDaily))).one()
    assert row.points_done == 1  # 视频算任务点
    assert row.quiz_done == 1  # 测验单独计
    assert row.watched_seconds == 90.0  # 只有视频计入观看时长


async def test_observer_ignores_progress_events(uow: UnitOfWork) -> None:
    """进度事件不参与统计（它每十几秒一条，会把时长算成心跳次数）。"""
    account_id = await make_account(uow)
    observer = ActivityObserver(StatsService(uow=uow))

    await observer.handle(
        TaskPointProgress(account_id=account_id, point_id="p", progress=30, duration=600)
    )

    async with uow.read() as session:
        assert list((await session.scalars(select(ActivityDaily))).all()) == []


async def test_observer_counts_failures_and_human_blocks(uow: UnitOfWork) -> None:
    """失败与待人工都计入失败数（它们都是"需要人看一眼"的信号）。"""
    account_id = await make_account(uow)
    observer = ActivityObserver(StatsService(uow=uow))

    await observer.handle(TaskPointFinished(account_id=account_id, kind="video", status="failed"))
    await observer.handle(
        TaskPointFinished(account_id=account_id, kind="video", status="need_human")
    )

    async with uow.read() as session:
        row = (await session.scalars(select(ActivityDaily))).one()
    assert row.failed_count == 2
    assert row.points_done == 0


async def test_kpi_reports_counts_and_scope(uow: UnitOfWork) -> None:
    """KPI 的数字要与库里的实际行数对得上。"""
    async with uow.begin() as session:
        account = Account(username="13800000000")
        session.add(account)
        await session.flush()
        course = Course(account_id=account.id, course_key="1:2", title="课", selected=True)
        session.add(course)
        await session.flush()
        session.add(
            TaskPoint(
                account_id=account.id,
                course_id=course.id,
                point_id="p1",
                kind="video",
                status="done",
            )
        )
        session.add(
            TaskPoint(
                account_id=account.id,
                course_id=course.id,
                point_id="p2",
                kind="video",
                status="queued",
            )
        )
        await session.flush()
        account_id = account.id

    stats = StatsService(uow=uow)
    await stats.bump(account_id, quizzes=2, watched_seconds=300)

    kpi = await stats.kpi(account_id=account_id)

    assert kpi["courses"] == 1
    assert kpi["selected_courses"] == 1
    assert kpi["points_total"] == 2
    assert kpi["points_done"] == 1
    assert kpi["points_queued"] == 1
    assert kpi["quizzes_done"] == 2
    assert kpi["watched_minutes"] == 5.0


async def test_kpi_on_empty_database_is_zero(uow: UnitOfWork) -> None:
    """空库时 KPI 全 0（不抛异常，图表能正常渲染）。"""
    kpi = await StatsService(uow=uow).kpi()

    assert kpi["courses"] == 0
    assert kpi["points_total"] == 0
    assert kpi["watched_minutes"] == 0
