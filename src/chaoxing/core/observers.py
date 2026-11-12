"""事件订阅者：把领域事件落到"四通道"（``docs/REFACTOR_PLAN.md`` §5.6）。

分工：

==================  ==========================  ================================
通道                 载体                         由谁负责
==================  ==========================  ================================
刷课记录             ``task_events``              :class:`TaskEventRecorder`
刷题记录             ``quiz_attempts``            quiz 服务直接写（这里只记事件）
行为审计             ``audit_logs``               用户操作中间件（P4）
app 日志              structlog                   :class:`DomainLogObserver`
==================  ==========================  ================================

**心跳进度故意不落库**：视频心跳每 12~20 秒一条，落库会写爆 SQLite 且毫无价值 ——
它只走日志与 SSE 实时通道。这正是"通道要分开"的实际意义。
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from chaoxing.obs.events import (
    AccountLoggedIn,
    CourseScanned,
    Event,
    HumanRequired,
    QuizAnswered,
    SessionExpired,
    TaskPointFinished,
    TaskPointProgress,
    TaskPointStarted,
)
from chaoxing.storage.models import TaskEvent
from chaoxing.storage.uow import UnitOfWork

if TYPE_CHECKING:
    from chaoxing.core.services.stats import StatsService

log = logging.getLogger(__name__)


def event_to_row(event: Event, *, account_id: int, course_id: int | None) -> dict[str, Any] | None:
    """把领域事件映射成 ``task_events`` 的一行。

    纯函数（便于离线回归）：**返回 None 表示这条事件不落库** ——
    视频进度就是这种情况，理由见模块 docstring。

    :param event: 领域事件
    :param account_id: 归属账号
    :param course_id: 归属课程（``0`` 与 ``None`` 都视为"不属于某门课"）
    :returns: 行数据；或不落库时返回 ``None``
    """
    if isinstance(event, TaskPointProgress):
        return None

    row: dict[str, Any] = {
        "account_id": account_id,
        # 事件里的默认值是 0，直接写库会撞外键约束（0 不是有效的课程 ID）
        "course_id": course_id or None,
        "task_point_id": None,
        "created_at": event.occurred_at,
    }

    if isinstance(event, TaskPointStarted):
        row.update(kind=event.kind, event="started", message=event.point_id)
    elif isinstance(event, TaskPointFinished):
        row.update(
            kind=event.kind,
            event="failed" if event.status in {"failed", "need_human"} else "finished",
            message=event.detail or event.point_id,
            detail={"status": event.status, "elapsed_s": event.elapsed_s},
        )
    elif isinstance(event, CourseScanned):
        row.update(event="scanned", message=event.course_key, detail={"points": event.point_count})
    elif isinstance(event, HumanRequired):
        row.update(event="need_human", message=event.detail or event.reason)
    elif isinstance(event, QuizAnswered):
        row.update(
            event="quiz",
            message=event.work_id,
            detail={
                "questions": event.question_count,
                "answered": event.answered,
                "submitted": event.submitted,
                "confidence": event.confidence,
            },
        )
    elif isinstance(event, AccountLoggedIn):
        row.update(event="login", message=event.username)
    elif isinstance(event, SessionExpired):
        row.update(event="expired", message=event.reason)
    else:
        return None
    return row


class TaskEventRecorder:
    """把刷课相关事件写进 ``task_events``（刷课记录通道）。

    :param uow: 工作单元
    """

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def handle(self, event: Event) -> None:
        """订阅回调。"""
        account_id = int(getattr(event, "account_id", 0) or 0)
        if not account_id:
            return
        course_id = getattr(event, "course_id", None)
        row = event_to_row(event, account_id=account_id, course_id=course_id)
        if row is None:
            return
        async with self._uow.begin() as session:
            session.add(TaskEvent(**row))


class DomainLogObserver:
    """把领域事件转成结构化日志（app 日志通道）。

    与 ``TaskEventRecorder`` 的区别：前者进库供查询与统计，这里只进日志文件，
    供"当时到底发生了什么"的时间线复盘。
    """

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._log = logger or logging.getLogger("chaoxing.events")

    async def handle(self, event: Event) -> None:
        """订阅回调。"""
        payload = {
            key: (value.isoformat() if hasattr(value, "isoformat") else value)
            for key, value in asdict(event).items()
        }
        self._log.info("domain_event %s", type(event).__name__, extra={"event": payload})


class ActivityObserver:
    """把任务点结果累加进按天统计（热力图与 KPI 的数据源）。

    两条口径判断：

    * **进度事件不计入** —— 它每 12~20 秒一条，累加会把"学习时长"算成心跳次数；
    * **测验与视频分开计** —— 观看时长只认视频，测验只计次数，否则 KPI 里的"学习时长"
      会被答题时间灌水。

    :param stats: 统计服务
    """

    def __init__(self, stats: StatsService) -> None:
        self._stats = stats

    async def handle(self, event: Event) -> None:
        """订阅回调。"""
        if not isinstance(event, TaskPointFinished):
            return
        account_id = int(event.account_id or 0)
        if not account_id:
            return

        if event.status == "done":
            is_quiz = event.kind in {"quiz", "exam"}
            await self._stats.bump(
                account_id,
                points=0 if is_quiz else 1,
                quizzes=1 if is_quiz else 0,
                watched_seconds=event.elapsed_s if event.kind == "video" else 0.0,
            )
        elif event.status in {"failed", "need_human"}:
            await self._stats.bump(account_id, failed=1)
