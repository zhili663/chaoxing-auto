"""事件 → 落库的映射（刷课记录通道）。

关键判断：**视频心跳进度不落库** —— 每 12~20 秒一条，落库会写爆 SQLite 且毫无价值，
它只该走日志与 SSE 实时通道。
"""

from __future__ import annotations

from sqlalchemy import select

from chaoxing.core.observers import DomainLogObserver, TaskEventRecorder, event_to_row
from chaoxing.obs.events import (
    AccountLoggedIn,
    CourseScanned,
    HumanRequired,
    QuizAnswered,
    SessionExpired,
    TaskPointFinished,
    TaskPointProgress,
    TaskPointStarted,
)
from chaoxing.storage.models import TaskEvent
from chaoxing.storage.uow import UnitOfWork


def test_progress_events_are_not_persisted() -> None:
    """心跳进度不落库（否则库里全是进度噪音）。"""
    row = event_to_row(
        TaskPointProgress(account_id=1, point_id="p", progress=10), account_id=1, course_id=2
    )

    assert row is None


def test_started_event_maps_to_row() -> None:
    """开始事件映射成 started 行。"""
    row = event_to_row(
        TaskPointStarted(account_id=1, course_id=2, point_id="p1", kind="video"),
        account_id=1,
        course_id=2,
    )

    assert row is not None
    assert row["event"] == "started"
    assert row["kind"] == "video"
    assert row["message"] == "p1"


def test_failed_and_need_human_events_are_marked_failed() -> None:
    """失败与需人工都记成 failed（前端筛"出了问题"时两者都该出现）。"""
    failed = event_to_row(
        TaskPointFinished(account_id=1, point_id="p", kind="quiz", status="failed", detail="3004"),
        account_id=1,
        course_id=None,
    )
    blocked = event_to_row(
        TaskPointFinished(account_id=1, point_id="p", kind="video", status="need_human"),
        account_id=1,
        course_id=None,
    )

    assert failed is not None and failed["event"] == "failed"
    assert blocked is not None and blocked["event"] == "failed"


def test_done_event_maps_to_finished() -> None:
    """完成事件记成 finished，并带上耗时。"""
    row = event_to_row(
        TaskPointFinished(account_id=1, point_id="p", kind="video", status="done", elapsed_s=12.5),
        account_id=1,
        course_id=3,
    )

    assert row is not None
    assert row["event"] == "finished"
    assert row["detail"]["elapsed_s"] == 12.5


def test_other_events_have_their_own_names() -> None:
    """其余事件的名称稳定（前端与统计按它过滤）。"""
    cases = [
        (CourseScanned(account_id=1, course_key="1:2", point_count=5), "scanned"),
        (HumanRequired(account_id=1, reason="captcha"), "need_human"),
        (QuizAnswered(account_id=1, work_id="w1", question_count=5, answered=5), "quiz"),
        (AccountLoggedIn(account_id=1, username="u"), "login"),
        (SessionExpired(account_id=1, reason="校验失败"), "expired"),
    ]

    for event, expected in cases:
        row = event_to_row(event, account_id=1, course_id=None)
        assert row is not None and row["event"] == expected, event


async def test_recorder_writes_task_events(uow: UnitOfWork, account_id: int) -> None:
    """订阅者真的把行写进库。"""
    recorder = TaskEventRecorder(uow)

    await recorder.handle(
        TaskPointFinished(account_id=account_id, point_id="p1", kind="video", status="done")
    )
    await recorder.handle(TaskPointProgress(account_id=account_id, point_id="p1", progress=30))

    async with uow.read() as session:
        rows = list((await session.scalars(select(TaskEvent))).all())

    assert len(rows) == 1  # 进度那条被过滤掉了
    assert rows[0].event == "finished"


async def test_recorder_ignores_events_without_account(uow: UnitOfWork) -> None:
    """没有账号归属的事件（系统级）不该落库到某个人头上。"""
    recorder = TaskEventRecorder(uow)

    await recorder.handle(TaskPointFinished(account_id=0, point_id="p"))

    async with uow.read() as session:
        assert list((await session.scalars(select(TaskEvent))).all()) == []


async def test_log_observer_does_not_raise(uow: UnitOfWork) -> None:
    """日志订阅者对任何事件都不应该抛异常（它会跑到刷课路径上）。"""
    observer = DomainLogObserver()

    await observer.handle(AccountLoggedIn(account_id=1, username="u"))
    await observer.handle(TaskPointProgress(account_id=1, point_id="p", progress=1.0))
