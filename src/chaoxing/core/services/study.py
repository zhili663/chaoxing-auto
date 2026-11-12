"""刷课编排：把"看视频 + 做题"压缩成一串有顺序的 API 调用。

执行模型（由**数据结构**保证，而不是靠调用顺序碰巧正确）：

1. 任务点按 ``sequence = 章序号 × 1000 + 章内序号`` 排序；
2. :func:`plan_units` 把它们切成"播放单元"：**视频开启新单元**，
   其后的非视频任务点并入当前单元；
3. 逐个单元执行 —— 于是"看完一个视频就先把紧随其后的题做完"是结构决定的。

视频部分只发心跳、**不下载任何视频流**（见 ``docs/REFACTOR_PLAN.md`` §5.4）。
时间轴推进用注入的 ``sleep`` 与 ``rng``：测试里零等待即可跑完整个视频。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import traceback
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select

from chaoxing.client import (
    ChaoxingTransport,
    ChapterApi,
    DocumentApi,
    QuizApi,
    SessionRegistry,
    SessionState,
    VideoApi,
)
from chaoxing.client.api.quiz import normalize_answer, sanitize_answers_by_type
from chaoxing.client.exceptions import ChaoxingError, HumanRequired
from chaoxing.client.model.base import ensure_not_error_page
from chaoxing.client.model.video import HeartbeatRequest
from chaoxing.client.parser.quiz import (
    align_questions_to_ids,
    extract_form_action,
    extract_hidden_inputs,
    extract_question_ids,
    parse_plain_questions,
    quiz_already_submitted,
)
from chaoxing.config import Settings
from chaoxing.core.services.answers import AnswerEngine
from chaoxing.core.services.courses import CourseService
from chaoxing.core.services.study_control import (
    CHECK_CONTINUE,
    Checkpoint,
    RunInterrupted,
    StudyControlService,
)
from chaoxing.obs.account_log import AccountLogWriter
from chaoxing.obs.events import (
    EventBus,
    QuizAnswered,
    StudyRunFinished,
    TaskPointFinished,
    TaskPointProgress,
    TaskPointStarted,
)
from chaoxing.obs.events import (
    HumanRequired as HumanRequiredEvent,
)
from chaoxing.storage.base import utcnow
from chaoxing.storage.models import Account, QuizAttempt, StudySession, TaskPoint
from chaoxing.storage.repositories import TaskPointRepo
from chaoxing.storage.uow import UnitOfWork

log = logging.getLogger(__name__)

#: 失败点允许被自动重选的次数上限（**总尝试次数**，不是额外重试次数）。
#:
#: 为什么要有上限：真机实测里被 403 拦下的测验每次 run 都被重新选中，重试到 3 次仍然失败 ——
#: 对**确定性拒绝**（换票被拦 / 章节未开放）反复请求只是白耗配额，也让 `task_events` 变得难读。
#: 到上限后要靠 `cx study reset`（清 retry_count）或修好根因再跑。
RETRY_LIMIT = 2

#: 支持的策略键（默认值在 :data:`chaoxing.core.services.plans.DEFAULT_STRATEGY`）
KIND_VIDEO = "video"
KIND_DOCUMENT = "document"
KIND_READ = "read"
KIND_QUIZ = "quiz"
KIND_EXAM = "exam"

#: 任务点类型的**规范列表**（与 ``TaskPoint.kind`` 的取值一致；`/meta/point-kinds` 直接读它）
#: 前五个有执行器；``discussion`` / ``unknown`` 会被 :meth:`StudyService._dispatch` 跳过
ALL_POINT_KINDS: tuple[str, ...] = (
    KIND_VIDEO,
    KIND_DOCUMENT,
    KIND_READ,
    KIND_QUIZ,
    KIND_EXAM,
    "discussion",
    "unknown",
)

#: 任务点状态（``TaskPoint.status`` 的取值；由刷课编排写入）
TASK_STATUSES: tuple[str, ...] = (
    "queued",
    "running",
    "done",
    "failed",
    "skipped",
    "need_human",
)


@dataclass(slots=True)
class PointView:
    """任务点的执行视图（从 ORM 抽出的纯数据，便于离线测试）。"""

    id: int
    point_id: str
    job_id: str = ""
    kind: str = "unknown"
    title: str = ""
    sequence: int = 0
    duration: float = 0.0
    progress: float = 0.0
    chapter_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    #: 所属账号（验证码接力端点要按任务点反查账号；``from_orm`` 会填上）
    account_id: int = 0

    @classmethod
    def from_orm(cls, point: TaskPoint) -> PointView:
        """由 ORM 对象构造。"""
        return cls(
            id=point.id,
            account_id=point.account_id,
            point_id=point.point_id,
            job_id=point.job_id,
            kind=point.kind,
            title=point.title,
            sequence=point.sequence,
            duration=point.duration,
            progress=point.progress,
            chapter_id=point.chapter_id,
            payload=dict(point.payload or {}),
        )


@dataclass(slots=True)
class StudyUnit:
    """一个播放单元：视频（或独立任务点）+ 紧随其后的任务点。"""

    head: PointView
    followers: list[PointView] = field(default_factory=list)

    @property
    def size(self) -> int:
        """单元内的任务点数量。"""
        return 1 + len(self.followers)


def plan_units(points: Sequence[PointView]) -> list[StudyUnit]:
    """把有序任务点切成播放单元（纯函数，见模块 docstring 的规则）。

    :param points: 任务点（无需预先排序）
    :returns: 播放单元列表，顺序与 ``sequence`` 一致
    """
    units: list[StudyUnit] = []
    current: StudyUnit | None = None

    for point in sorted(points, key=lambda item: (item.sequence, item.id)):
        if point.kind == KIND_VIDEO or current is None:
            current = StudyUnit(head=point)
            units.append(current)
        else:
            current.followers.append(point)

    return units


@dataclass(slots=True)
class StudyReport:
    """一轮刷课的结果。"""

    account_id: int = 0
    units: int = 0
    done: int = 0
    failed: int = 0
    blocked: int = 0
    skipped: int = 0
    #: 是否按要求**提前收尾**（0/1）——暂停或中止都算，绝不静默
    aborted: int = 0
    #: 提前收尾的原因（控制状态码；未提前收尾时为空）
    abort_reason: str = ""
    elapsed_s: float = 0.0
    details: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        """一行式摘要。"""
        tail = f" · 提前收尾 {self.abort_reason}" if self.aborted else ""
        return (
            f"完成 {self.done} · 失败 {self.failed} · 待人工 {self.blocked} · "
            f"跳过 {self.skipped}（{self.elapsed_s:.1f}s）{tail}"
        )


#: 换票成功后可能落到的几种作业页形态（真机实测都见过）
_WORK_URL_MARKERS: tuple[str, ...] = (
    "doHomeWork",  # 经典题目页
    "selectWorkQuestion",  # 新版明文题接口（换票直接落到它也算成功）
    "api/work",  # 换票接口自身（带 api=1 时）
    "work/phone/",  # 手机 App 题目页/入口
)


def _looks_like_work_url(url: str) -> bool:
    """换票后是否落到了作业相关页面。

    别写死成单个路径：真机上换票的落点至少见过三种形态。早先只认 ``doHomeWork``，
    于是"换票明明成功、却报换票失败" —— 而那个报错把真正的失败原因盖住了，
    排查时会往错误的方向走。

    :param url: 换票后的最终地址
    :returns: 像作业页则为真
    """
    return any(marker in url for marker in _WORK_URL_MARKERS)


@dataclass(slots=True)
class ActiveRun:
    """一个正在刷课的账号（仪表盘「并发刷课」模块的数据行）。"""

    account_id: int
    username: str = ""
    point_title: str = ""
    kind: str = ""
    progress: float = 0.0
    duration: float = 0.0
    started_at: datetime | None = None
    updated_at: datetime | None = None


class StudyService:
    """刷课编排服务。

    :param uow: 工作单元
    :param transport: 学习通传输层
    :param registry: 会话注册表
    :param bus: 事件总线
    :param settings: 全局配置（心跳区间、倍速、并发等）
    :param sleep: 可注入的等待函数（测试里替换成零等待）
    :param rng: 可注入的随机源（测试里固定种子）
    :param control: 控制面（暂停/继续/中止信号）；缺省时按同一个 UoW 建一个
    """

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        transport: ChaoxingTransport,
        registry: SessionRegistry,
        bus: EventBus,
        settings: Settings,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
        answers: AnswerEngine | None = None,
        control: StudyControlService | None = None,
        courses: CourseService | None = None,
        account_log: AccountLogWriter | None = None,
    ) -> None:
        self._uow = uow
        self._transport = transport
        self._registry = registry
        self._bus = bus
        self._settings = settings
        self._sleep = sleep
        self._rng = rng or random.Random()
        # 控制面：CLI 的 pause/resume 与 API 的路由都读它，所以只建一份跟着服务走
        self.control = control or StudyControlService(uow=uow)
        # 课程服务：测验执行前用它就地刷新票据（扫描时存的 enc 几分钟就轮换）。
        # 未注入时退化为"不刷新"（沿用旧 payload），不影响其它链路。
        self._courses = courses
        # 按账号隔离的日志（容器注入；测试不注入则不落盘，避免污染仓库 data/logs）
        self._account_log = account_log
        self._video = VideoApi(transport)
        self._document = DocumentApi(transport)
        self._chapter = ChapterApi(transport)
        self._quiz_api = QuizApi(transport)
        # 答案引擎：缓存优先，未配置模型时也能靠题库跑（没配就是"只读缓存"）
        self._answers = answers or AnswerEngine(uow=uow)

    # ------------------------------------------------------------------ #
    # 主流程
    # ------------------------------------------------------------------ #
    def _acct_log(self, account_id: int, event: str, **fields: Any) -> None:
        """写按账号隔离的日志（未注入 writer 时静默）。"""
        if self._account_log is not None:
            self._account_log.write(account_id, event, **fields)

    @staticmethod
    def _error_fields(exc: BaseException) -> dict[str, Any]:
        """把异常的可观测字段抽出来（类型 / 消息 / 原始 payload / marker）。"""
        fields: dict[str, Any] = {
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        payload = getattr(exc, "payload", None)
        if payload:
            fields["payload"] = payload
        marker = getattr(exc, "marker", None)
        if marker:
            fields["marker"] = marker
        reason = getattr(exc, "reason", None)
        if reason:
            fields["reason"] = reason
        return fields

    async def run_account(
        self,
        account_id: int,
        *,
        course_ids: Sequence[int] | None = None,
        limit: int | None = None,
    ) -> StudyReport:
        """刷一个账号的课（记录一次 ``study_sessions``，供仪表盘显示活跃账号）。"""
        session_id = await self._start_session(account_id)
        # 新 run 先换一个出口：不沿用上个 run 的 IP；只有一条出口时无实际变化。
        rotate_proxy = getattr(self._transport, "rotate_proxy", None)
        if rotate_proxy is not None:
            rotate_proxy(account_id)
        report = StudyReport(account_id=account_id)
        failed = False
        error_text = ""
        self._acct_log(
            account_id,
            "run_started",
            course_ids=list(course_ids) if course_ids is not None else None,
            limit=limit,
        )
        try:
            report = await self._run_account_body(account_id, course_ids=course_ids, limit=limit)
            return report
        except BaseException as exc:
            failed = True
            error_text = traceback.format_exc()
            self._acct_log(
                account_id,
                "run_crashed",
                **self._error_fields(exc),
                traceback=error_text,
            )
            raise
        finally:
            await self._finish_session(session_id, report, failed=failed)
            self._acct_log(
                account_id,
                "run_finished",
                units=report.units,
                done=report.done,
                failed=report.failed,
                blocked=report.blocked,
                skipped=report.skipped,
                aborted=report.aborted,
                abort_reason=report.abort_reason,
                elapsed_s=round(report.elapsed_s, 2),
                error=error_text,
            )

    async def _run_account_body(
        self,
        account_id: int,
        *,
        course_ids: Sequence[int] | None = None,
        limit: int | None = None,
    ) -> StudyReport:
        """刷课主体（不含会话记录；由 :meth:`run_account` 包一层）。

        执行前与每个播放单元之前都过一次控制检查点：命中暂停/中止信号就**提前收尾**
        并在报告里写清原因（已完成的进度原样留在库里，下次 run 从那里继续）。

        :param account_id: 账号 ID
        :param course_ids: 要刷的课程（缺省 = 该账号全部未完成任务点）
        :param limit: 最多执行多少个任务点（``None`` = 不限，用于试跑）
        :returns: :class:`StudyReport`
        """
        started = time.monotonic()
        report = StudyReport(account_id=account_id)

        points = await self._load_points(account_id, course_ids)
        if limit is not None:
            points = points[:limit]
        units = plan_units(points)
        report.units = len(units)
        log.info(
            "账号 %s：%d 个待处理任务点，切成 %d 个播放单元", account_id, len(points), len(units)
        )

        concurrency = max(1, int(self._settings.chaoxing.video_concurrency))
        if concurrency > 1:
            await self._run_units_concurrently(account_id, units, report, concurrency)
        else:
            for index, unit in enumerate(units):
                # 检查点①：单元边界（也是"本次 run 的起点"，paused 在这里就被挡住）
                verdict = await self.control.checkpoint(account_id)
                if verdict.action != CHECK_CONTINUE:
                    self._abort(report, verdict, remaining=len(units) - index)
                    break
                # 单元边界：按请求数/粘性时长主动换出口（不在心跳中途切 IP）
                rotate_if_due = getattr(self._transport, "rotate_proxy_if_due", None)
                if rotate_if_due is not None:
                    rotate_if_due(account_id)
                try:
                    await self._run_unit(account_id, unit, report)
                except RunInterrupted as exc:
                    # 检查点②在 _beat 里：视频心跳中途被要求停下
                    self._abort(report, exc.verdict, remaining=len(units) - index - 1)
                    break

        report.elapsed_s = time.monotonic() - started
        self._bus.publish(
            StudyRunFinished(
                account_id=account_id,
                done=report.done,
                failed=report.failed,
                blocked=report.blocked,
            )
        )
        return report

    async def _start_session(self, account_id: int) -> int:
        """创建一次 running 的 ``study_sessions`` 记录；同账号遗留 running 先标 aborted。

        进程被 kill 时 finally 不会执行，下一次 run 在这里回收，避免仪表盘永远显示
        "正在刷课"。
        """
        async with self._uow.begin() as db:
            stale = list(
                (
                    await db.scalars(
                        select(StudySession).where(
                            StudySession.account_id == account_id,
                            StudySession.status == "running",
                        )
                    )
                ).all()
            )
            for row in stale:
                row.status = "aborted"
                row.finished_at = utcnow()
            session = StudySession(
                account_id=account_id,
                status="running",
                started_at=utcnow(),
                summary={},
            )
            db.add(session)
            await db.flush()
            return session.id

    async def _finish_session(self, session_id: int, report: StudyReport, *, failed: bool) -> None:
        """结束会话（异常也走这里；失败/提前收尾记 aborted，不伪装成功）。"""
        async with self._uow.begin() as db:
            row = await db.get(StudySession, session_id)
            if row is None or row.status != "running":
                return
            row.status = "aborted" if failed or report.aborted else "finished"
            row.finished_at = utcnow()
            row.summary = {
                "done": report.done,
                "failed": report.failed,
                "blocked": report.blocked,
                "skipped": report.skipped,
                "aborted": report.aborted,
                "reason": report.abort_reason,
            }

    async def active_accounts(self, *, owner_user_id: int | None = None) -> list[ActiveRun]:
        """当前正在刷课的账号（``study_sessions.status='running'``）。

        :param owner_user_id: 只统计该属主的账号（``None`` = 不限）
        :returns: 活跃账号列表（含当前运行中的任务点标题与进度）
        """
        async with self._uow.read() as db:
            statement = (
                select(StudySession, Account)
                .join(Account, Account.id == StudySession.account_id)
                .where(StudySession.status == "running")
                .order_by(StudySession.started_at)
            )
            if owner_user_id is not None:
                statement = statement.where(Account.owner_user_id == owner_user_id)
            rows = (await db.execute(statement)).all()
            runs: list[ActiveRun] = []
            for session, account in rows:
                point = (
                    await db.scalars(
                        select(TaskPoint)
                        .where(
                            TaskPoint.account_id == session.account_id,
                            TaskPoint.status == "running",
                        )
                        .order_by(TaskPoint.updated_at.desc())
                        .limit(1)
                    )
                ).first()
                runs.append(
                    ActiveRun(
                        account_id=session.account_id,
                        username=account.username,
                        point_title=point.title if point is not None else "",
                        kind=point.kind if point is not None else "",
                        progress=float(point.progress) if point is not None else 0.0,
                        duration=float(point.duration) if point is not None else 0.0,
                        started_at=session.started_at,
                        updated_at=(point.updated_at if point is not None else session.updated_at),
                    )
                )
            return runs

    async def _run_units_concurrently(
        self,
        account_id: int,
        units: Sequence[StudyUnit],
        report: StudyReport,
        concurrency: int,
    ) -> None:
        """并发推进多个播放单元（信号量限流；检查点仍在**单元边界**生效）。

        与串行分支的唯一区别是推进方式：每个单元内部依旧是"先视频、再紧随其后的任务点"，
        所以"看完一个视频就先把紧随其后的题做完"这条结构保证不变 —— 变的只是有 N 个单元
        同时在推（每条视频各自按真实时间打心跳，互不干扰）。

        暂停语义：检查点命中后不再启动新单元，已在飞的单元跑完当前这一轮就停，
        进度全部留在库里，下次 run 从断点继续。

        :param account_id: 账号 ID
        :param units: 播放单元（顺序与 ``sequence`` 一致）
        :param report: 报告（``_count`` 之间没有 await，因此并发下自增是安全的）
        :param concurrency: 同时在飞的单元数
        """
        semaphore = asyncio.Semaphore(concurrency)
        stopped = False

        async def worker(index: int, unit: StudyUnit) -> None:
            nonlocal stopped
            async with semaphore:
                if stopped:
                    return
                verdict = await self.control.checkpoint(account_id)
                if verdict.action != CHECK_CONTINUE:
                    stopped = True
                    self._abort(report, verdict, remaining=len(units) - index)
                    return
                rotate_if_due = getattr(self._transport, "rotate_proxy_if_due", None)
                if rotate_if_due is not None:
                    rotate_if_due(account_id)
                try:
                    await self._run_unit(account_id, unit, report)
                except RunInterrupted as exc:
                    stopped = True
                    self._abort(report, exc.verdict, remaining=len(units) - index - 1)

        await asyncio.gather(*(worker(index, unit) for index, unit in enumerate(units)))

    def _abort(self, report: StudyReport, verdict: Checkpoint, *, remaining: int) -> None:
        """记下"按要求提前收尾"（暂停/中止都走这里，绝不静默）。"""
        report.aborted += 1
        report.abort_reason = verdict.state
        report.details.append(
            f"[{verdict.state}] 提前收尾：{verdict.note}（还有 {remaining} 个播放单元未执行）"
        )
        log.info(
            "账号 %s 按要求提前收尾：%s（剩 %d 个单元）", report.account_id, verdict.note, remaining
        )

    async def _run_unit(self, account_id: int, unit: StudyUnit, report: StudyReport) -> None:
        """执行一个播放单元：先视频，再紧随其后的任务点。"""
        status = await self._dispatch(account_id, unit.head)
        self._count(report, unit.head, status)

        for follower in unit.followers:
            follower_status = await self._dispatch(account_id, follower)
            self._count(report, follower, follower_status)

    def _count(self, report: StudyReport, point: PointView, status: str) -> None:
        """把单个任务点的结果计入报告。"""
        if status in {"done", "already_done"}:
            report.done += 1
        elif status == "need_human":
            report.blocked += 1
        elif status == "skipped":
            report.skipped += 1
        else:
            report.failed += 1
        report.details.append(f"[{status}] {point.kind} {point.title or point.point_id}")

    async def _dispatch(self, account_id: int, point: PointView) -> str:
        """按类型分发到一个任务点的执行器。"""
        self._bus.publish(
            TaskPointStarted(
                account_id=account_id,
                point_id=point.point_id,
                kind=point.kind,
            )
        )
        await self._mark(point.id, "running")
        self._acct_log(
            account_id,
            "task_started",
            point_id=point.point_id,
            job_id=point.job_id,
            kind=point.kind,
            title=point.title,
            course_id=point.payload.get("courseId"),
            chapter_id=point.chapter_id,
            work_id=point.payload.get("workid"),
        )

        started = time.monotonic()
        try:
            if point.kind == KIND_VIDEO:
                status = await self._run_video(account_id, point)
            elif point.kind in {KIND_DOCUMENT, KIND_READ}:
                status = await self._run_document(account_id, point)
            elif point.kind in {KIND_QUIZ, KIND_EXAM}:
                status = await self._run_quiz(account_id, point)
            else:
                status = "skipped"
        except HumanRequired as exc:
            await self._mark(point.id, "need_human", error=str(exc))
            self._acct_log(
                account_id,
                "task_need_human",
                point_id=point.point_id,
                kind=point.kind,
                title=point.title,
                **self._error_fields(exc),
                traceback=traceback.format_exc(),
            )
            self._bus.publish(
                HumanRequiredEvent(account_id=account_id, reason=exc.reason, detail=str(exc))
            )
            return "need_human"
        except ChaoxingError as exc:
            await self._mark(point.id, "failed", error=str(exc), bump_retry=True)
            self._acct_log(
                account_id,
                "task_failed",
                point_id=point.point_id,
                kind=point.kind,
                title=point.title,
                **self._error_fields(exc),
                traceback=traceback.format_exc(),
            )
            # 主日志只留一行摘要；完整 payload / traceback 在账号日志里
            log.warning(
                "账号 %s 任务点 %s 失败（%s）：%s",
                account_id,
                point.point_id,
                type(exc).__name__,
                exc,
            )
            self._finish(account_id, point, "failed", str(exc), time.monotonic() - started)
            return "failed"
        except BaseException as exc:
            self._acct_log(
                account_id,
                "task_crashed",
                point_id=point.point_id,
                kind=point.kind,
                title=point.title,
                **self._error_fields(exc),
                traceback=traceback.format_exc(),
            )
            raise

        await self._mark(
            point.id,
            "done" if status in {"done", "already_done"} else status,
            error="" if status in {"done", "already_done"} else status,
        )
        self._finish(account_id, point, status, "", time.monotonic() - started)
        self._acct_log(
            account_id,
            "task_finished",
            point_id=point.point_id,
            kind=point.kind,
            title=point.title,
            status=status,
            elapsed_s=round(time.monotonic() - started, 2),
        )
        return status

    def _finish(
        self, account_id: int, point: PointView, status: str, detail: str, elapsed: float
    ) -> None:
        """发布任务点结束事件。"""
        self._bus.publish(
            TaskPointFinished(
                account_id=account_id,
                point_id=point.point_id,
                kind=point.kind,
                status="done" if status == "already_done" else status,
                detail=detail,
                elapsed_s=elapsed,
            )
        )

    # ------------------------------------------------------------------ #
    # 视频
    # ------------------------------------------------------------------ #
    async def _run_video(self, account_id: int, point: PointView) -> str:
        """视频：拿 dtoken → 按节奏推心跳 → 以服务端回执判定完成。"""
        session = self._session_for(account_id)

        info = await self._video.fetch_info(session, object_id=point.point_id)
        if not info.usable:
            raise ChaoxingError(f"视频 {point.point_id} 没有拿到 dtoken，无法上报进度")
        duration = info.duration or point.duration
        if duration <= 0:
            raise ChaoxingError(f"视频 {point.point_id} 时长为 0，无法推进进度")

        # ★ 倍率只认配置（settings.chaoxing.video_rate，线上是 1.0）：
        #   卡片 payload 里的 rate / doublespeed 是"允许用户倍速播放"的声明，不是我们应该上报的倍率。
        #   照它上报会系统性多报（实测：22 分钟的视频 2 分钟推完 → 平台判为不可能 → 不给完成标记）。
        configured_rate = float(self._settings.chaoxing.video_rate)
        # 线上统一 1.0：即使旧 .env/DB 还留着 2.0，也按真实时间上报；
        # 高倍率会让服务端永远不给 isPassed，反而白跑。
        rate = min(configured_rate, 1.0)
        if configured_rate > 1.0:
            log.warning(
                "video_rate=%.2f 已按 1.0x 执行（服务端核对真实经过时间；高倍速拿不到完成标记）",
                configured_rate,
            )
        interval_lo = self._settings.chaoxing.heartbeat_interval_min
        interval_hi = self._settings.chaoxing.heartbeat_interval_max
        max_step = self._settings.chaoxing.max_progress_step

        current = max(point.progress, 0.0)
        while current < duration:
            # ★ 进度必须与**真实等待时间**严格对应：服务端会核对"上报进度 vs 真实经过时间"，
            #   多报一点（哪怕 25%）就拿不到完成标记（心跳仍 200，但回执永远 isPassed:false）。
            #   实测见 docs/MOOC_ANS_403.md §六 与 config.video_rate 的注释。
            waited = self._rng.uniform(interval_lo, interval_hi)
            await self._sleep(waited)
            current = min(current + min(waited * rate, max_step), duration)
            verdict = await self._beat(
                account_id, session, point, current, duration, rate, info.dtoken
            )

            if verdict.needs_human:
                raise HumanRequired("视频要求人脸抓拍", reason="face")
            if verdict.forbidden:
                raise ChaoxingError("心跳被拒（403）：签名或 dtoken 可能已过期")
            if verdict.passed:
                return "done"

            await self._mark(point.id, "running", progress=current)
            self._bus.publish(
                TaskPointProgress(
                    account_id=account_id,
                    point_id=point.point_id,
                    progress=current,
                    duration=duration,
                )
            )

        # 时间轴走完 → 收尾心跳（这一步决定"本地跑完"能不能变成"服务端认可"）
        verdict = await self._beat(
            account_id, session, point, duration, duration, rate, info.dtoken
        )
        return "done" if verdict.passed else "failed"

    async def _beat(
        self,
        account_id: int,
        session: SessionState,
        point: PointView,
        current: float,
        duration: float,
        rate: float,
        dtoken: str,
    ) -> Any:
        """发一次心跳（参数全部来自卡片透传字段）。

        发之前先过一次控制检查点：被要求暂停/中止时抛 :class:`RunInterrupted`，
        由 :meth:`run_account` 收尾 —— **不推进时间轴、不丢已上报的进度**。
        """
        verdict = await self.control.checkpoint(account_id)
        if verdict.action != CHECK_CONTINUE:
            raise RunInterrupted(verdict)

        payload = point.payload
        # ★ attDuration / attDurationEnc 必须带上：它们与 otherInfo 一样**不在附件元素上**，
        # 而在页面 JSON 里（已由解析层注入 payload）。真机抓到的浏览器心跳里这两个都有：
        #     attDuration=441 & attDurationEnc=38abb0e7...
        # 缺了它们服务端 403，而 403 页面不给任何原因 —— 只能靠逐参数对比才发现。
        extra: dict[str, str] = {}
        for name in ("attDuration", "attDurationEnc", "courseEngineInfo"):
            value = payload.get(name)
            if value in (None, ""):
                continue
            if isinstance(value, float) and value.is_integer():
                extra[name] = str(int(value))  # 441.0 → "441"（真机发的是整数形态）
            else:
                extra[name] = str(value)
        request = HeartbeatRequest(
            clazz_id=str(payload.get("clazzId", "")),
            user_id=str(payload.get("userid", "")),
            job_id=point.job_id,
            object_id=point.point_id,
            course_id=str(payload.get("courseId", "")),
            other_info=str(payload.get("otherInfo", "")),
            playing_time=current,
            duration=duration,
            rate=str(rate),
            cpi=str(payload.get("cpi", "")),
            dtoken=dtoken,
            extra=extra,
        )
        return await self._video.heartbeat(session, request)

    # ------------------------------------------------------------------ #
    # 文档 / 阅读
    # ------------------------------------------------------------------ #
    async def _run_document(self, account_id: int, point: PointView) -> str:
        """文档 / PPT / 阅读：调完成接口，**以响应体 status 为准**。"""
        session = self._session_for(account_id)
        payload = point.payload
        # 「阅读 / 专题阅读」卡走**真机实测的完成上报**：2026-10-12 抓包确认浏览器打开阅读卡时调
        # /mooc-ans/coursedata/readjobv2/show，用它带的 enc 上报；经典 /ananas/job/* 对这类卡
        # 一律「无效的请求参数」。**判据是"卡片带 enc"**，不是 jobid 前缀 —— 真机数据里只有部分
        # 阅读卡的 jobid 形如 read-<数字>，另一些是纯数字 id（`#178/#180/#181` 就是这样被漏掉的）。
        # 这一段必须排在 jtoken 守卫之前：这类卡没有 jtoken。
        enc = str(payload.get("enc", ""))
        jtoken = str(payload.get("jtoken", ""))
        # PPT/文档卡也可能带 enc；有 jtoken 时必须先走 /mooc-ans/job/document。
        # 只有「enc 且没有 jtoken」才是阅读/专题阅读卡的 readjobv2 流程。
        if enc and not jtoken and not str(payload.get("workid", "")):
            readv2_html = await self._document.finish_read_v2(
                session,
                job_id=point.job_id,
                knowledge_id=point.chapter_id or str(payload.get("knowledgeid", "")),
                course_id=str(payload.get("courseId", "")),
                clazz_id=str(payload.get("clazzId", "")),
                cpi=str(payload.get("cpi", "")),
                enc=enc,
            )
            # readv2 返回 HTML；登录页/3004 错误页也不带 status，先过错误页指纹。
            ensure_not_error_page(readv2_html)
            # ★ readv2 返回 HTML，**不能**凭它判完成；必须回目录页核对平台进度。
            if not await self._document_progress_done(account_id, point):
                raise ChaoxingError(
                    f"文档/PPT {point.point_id} readv2 已调用，但平台进度仍未标记完成"
                )
            return "done"

        if not jtoken:
            # 缺 jtoken 会拿到 200 + status=false —— 早点说清楚，别等它失败
            raise ChaoxingError(f"任务点 {point.job_id} 缺 jtoken（章节卡片里没有？）")

        args = {
            "job_id": point.job_id,
            "knowledge_id": point.chapter_id or str(payload.get("knowledgeid", "")),
            "course_id": str(payload.get("courseId", "")),
            "clazz_id": str(payload.get("clazzId", "")),
            "jtoken": jtoken,
        }
        envelope = await self._document.finish_document(session, **args)
        if "已经完成" in envelope.reason:
            # 服务端已经明确说完成了：不要再拿目录页复核去否掉它，
            # 否则多任务点章节/目录页版本差异会把已完成点标成 failed。
            return "already_done"
        if not await self._document_progress_done(account_id, point):
            raise ChaoxingError(f"文档/PPT {point.point_id} 完成接口已返回，但平台进度仍未标记完成")
        return "done"

    async def _document_progress_done(self, account_id: int, point: PointView) -> bool:
        """回课程目录页核对任务点是否真的被平台标记完成。

        ``finish_document`` / ``finish_read_v2`` 的响应只说明"接口被调用"，
        不代表目录页进度已更新；这正是"PPT 显示完成、平台仍红点"的来源。
        注入 ``CourseService`` 时才核对（测试/离线调用没有目录页可读）。
        """
        if self._courses is None:
            return True
        payload = point.payload
        course_id = str(payload.get("courseId", ""))
        clazz_id = str(payload.get("clazzId", ""))
        if not course_id or not clazz_id:
            return True
        chapter = point.chapter_id or str(payload.get("knowledgeid", ""))
        if not chapter:
            return False
        try:
            report = await self._courses.sync_progress(account_id, f"{course_id}:{clazz_id}")
        except Exception as exc:
            log.warning("文档 %s 完成后同步进度失败：%s", point.point_id, exc)
            return False
        # 只看**本次目录页**的完成映射；不能读旧 TaskPoint.status ——
        # sync_progress 会把整章任务点批量置 done，旧状态可能正是误判留下的。
        if report.chapters and chapter in report.done_chapters:
            return True
        self._acct_log(
            account_id,
            "document_unverified",
            point_id=point.point_id,
            kind=point.kind,
            title=point.title,
            chapter_id=chapter,
            chapters=report.chapters,
            remote_done=report.remote_done,
            marked=report.marked,
        )
        return False

    # ------------------------------------------------------------------ #
    # 测验 / 考试
    # ------------------------------------------------------------------ #
    async def _open_quiz_page(
        self,
        account_id: int,
        session: SessionState,
        point: PointView,
        work_id: str,
        payload: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]]:
        """换票并取题目页；票据失效/3004 时**刷新一次再重试**。

        参考实现（Samueli924）对换票失败会重试抓取；本项目真机上的 403 根因是
        ``ktoken`` / ``enc`` 过期（``docs/MOOC_ANS_403.md`` §7.1）。调用方已经刷新过
        一次，但扫描与执行之间仍可能再次过期，或撞上一次 3004 错误页 ——
        这时唯一有效的动作不是重放同一组旧票据，而是**重新扫卡拿新票据**再换票。
        只重试一次：确定性拒绝（章节未开放）重试再多也只是白耗请求。

        :param account_id: 账号 ID
        :param session: 账号会话
        :param point: 任务点视图
        :param work_id: 数字作业 ID
        :param payload: 当前票据
        :returns: ``(最终题目页 URL, 题目页 HTML, 重试后使用的 payload)``
        """
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                final_url = await self._quiz_redirect(session, point, work_id)
                if not _looks_like_work_url(final_url):
                    raise ChaoxingError(f"测验 {work_id} 换票没落到作业页：{final_url}")
                detail_html = await self._quiz_api.fetch_detail_html(
                    session, work_id=work_id, url=final_url
                )
                return final_url, detail_html, payload
            except ChaoxingError as exc:
                last_error = exc
                if attempt == 0:
                    # 手机 App 链路（mworkspecial → work/phone/work）与 Web 换票是两条路。
                    # Web 403/3004 时尝试手机入口；未交卷手机页的 form1 会指向
                    # /mooc-ans/work/addStudentWorkNew，提交层会原样使用 action。
                    try:
                        phone_url, phone_html = await self._quiz_api.fetch_phone_work(
                            session,
                            work_id=str(payload.get("workid", "") or work_id),
                            job_id=point.job_id,
                            course_id=str(payload.get("courseId", "")),
                            clazz_id=str(payload.get("clazzId", "")),
                            knowledge_id=point.chapter_id or str(payload.get("knowledgeid", "")),
                            cpi=str(payload.get("cpi", "")),
                            ktoken=str(payload.get("ktoken", "")),
                            enc=str(payload.get("enc", "")),
                            user_id=str(payload.get("userid", "")),
                        )
                        if _looks_like_work_url(phone_url):
                            log.info("测验 %s Web 失败，已切到手机题目页", work_id)
                            return phone_url, phone_html, payload
                    except ChaoxingError as phone_exc:
                        last_error = phone_exc
                        log.warning("测验 %s 手机入口也失败：%s", work_id, phone_exc)
                    log.warning("测验 %s 换票/取题失败（%s），刷新票据后重试一次", work_id, exc)
                    # 抖动后再扫卡：既避开同一秒的限流窗口，也避免与其它账号同步撞点。
                    await self._sleep(1.0 + self._rng.random() * 0.7)
                    payload = await self._refresh_quiz_payload(account_id, point)
                    point.payload = payload
        if last_error is not None:
            raise last_error
        raise ChaoxingError(f"测验 {work_id} 换票失败")

    async def _run_quiz(self, account_id: int, point: PointView, *, input_code: str = "") -> str:
        """测验：换票 → 取题 → 作答 → 提交。

        两条数据来源**缺一不可**：

        * 题目页（``doHomeWorkNew``）给**题目 ID 与隐藏域** —— 提交报文要用，
          而且题目 ID 只在这里有；
        * 明文题接口给**可读题干** —— 题目页的题干被字体加密（``font-cxsecret``），
          模型读到的会是乱码。

        两侧顺序并不总是相同，因此按页面 ``answertype{id}`` 与明文题题型做保序匹配
        （见 :func:`~chaoxing.client.parser.quiz.align_questions_to_ids`）。

        换票的 403 根因已定论（``docs/MOOC_ANS_403.md`` §7.1）：**扫描时存的
        ``ktoken`` 过期**，不是会话上下文。执行前会重扫卡片刷新票据；若仍然
        403 或落到 3004 错误页，:meth:`_open_quiz_page` 会再刷新一次并重试。
        """
        session = self._session_for(account_id)
        payload = point.payload
        work_id = str(payload.get("workid", "") or point.point_id)
        if not work_id:
            raise ChaoxingError(f"测验 {point.point_id} 没有 workid")
        if not payload.get("workid"):
            # 没有 workid 时退回 job_id 只是权宜 —— 记一笔，方便发现扫描把 workid 丢了
            log.warning("测验 %s 缺 workid，退回 job_id 换票（扫描可能没存上）", point.point_id)

        # 扫描时存的票据会很快失效（真机实测：enc 几分钟就轮换）——执行前就地刷新。
        # 刷新失败就沿用旧 payload（失败要软，不能因为刷新不成功就让任务点直接失败）。
        payload = await self._refresh_quiz_payload(account_id, point)
        point.payload = payload

        final_url, detail_html, payload = await self._open_quiz_page(
            account_id, session, point, work_id, payload
        )
        point.payload = payload
        if quiz_already_submitted(detail_html):
            log.info("测验 %s 已交卷，跳过", work_id)
            return "already_done"

        question_ids = extract_question_ids(detail_html)
        hidden_fields = extract_hidden_inputs(detail_html)
        form_action = extract_form_action(detail_html)
        #: 页面逐题的 ``answertype{id}`` —— 对齐明文题与答案形状都要它（真机 2026-10-16）
        page_types = {
            key[len("answertype") :]: value
            for key, value in hidden_fields.items()
            if key.startswith("answertype")
        }
        # ★ 两处 ID 来源取并集：题目容器可能只在部分题上写 id=questionN，
        #   而逐题的 answertypeN 隐藏域通常覆盖整卷。漏掉后者会静默只答前 5 题。
        if len(page_types) > len(question_ids):
            question_ids += [qid for qid in page_types if qid not in question_ids]
        form_ids = list(dict.fromkeys(question_ids))
        plain_html = await self._quiz_api.fetch_plain_questions_html(
            session,
            course_id=str(payload.get("courseId", "")),
            work_id=work_id,
            job_id=point.job_id,
            clazz_id=str(payload.get("clazzId", "")),
            cpi=str(payload.get("cpi", "")),
            knowledge_id=point.chapter_id,
            ktoken=str(payload.get("ktoken", "")),
        )
        # ★ 按**题型**对齐（真机 2026-10-16）：明文题与题目页顺序并不总是相同，
        #   按索引合并会把判断题配上多选答案 → 服务端整卷拒绝。
        questions = align_questions_to_ids(
            parse_plain_questions(plain_html), question_ids, page_types
        )
        if not questions:
            raise ChaoxingError(f"测验 {work_id} 没解析到题目（页面结构可能变了）")
        unidentified = [item for item in questions if not item.question_id]
        if unidentified:
            self._acct_log(
                account_id,
                "quiz_id_mismatch",
                work_id=work_id,
                questions=len(questions),
                form_ids=len(form_ids),
                page_types=len(page_types),
                unidentified=len(unidentified),
            )
            if self._settings.chaoxing.quiz_require_full_answers:
                raise ChaoxingError(
                    f"测验 {work_id} 的题目 ID 不完整：{len(form_ids)} 个 ID / "
                    f"{len(questions)} 题，拒绝只答一部分"
                )
            log.warning("测验 %s 有 %d 题无 ID（允许部分提交）", work_id, len(unidentified))

        answers: dict[str, str] = {}
        confidences: list[float] = []
        for question in questions:
            if not question.question_id or not question.answerable:
                continue
            answer = await self._answers.answer(question)
            if not answer.usable:
                continue
            normalized = normalize_answer(question.qtype_hint, answer.value)
            if normalized is None:
                # 认不出格式就**留空这一题**，别把臆造的字符串发上去。
                # 实测（2026-10-13，`#19`）：连线题留空、其余题正常，服务端照样回 success!；
                # 整卷都认不出时下面的"一题都没答出来"会兜住。
                log.warning(
                    "测验 %s 第 %s 题（%s）作答无法规范化，留空：%r",
                    work_id,
                    question.question_id,
                    question.qtype_hint or question.qtype.value,
                    answer.value,
                )
                continue
            answers[f"answer{question.question_id}"] = normalized
            confidences.append(answer.confidence)

        if not answers:
            raise ChaoxingError(f"测验 {work_id} 一题都没答出来（共 {len(questions)} 题）")

        # ★ 答案形状必须与页面 answertype 一致（真机实测 2026-10-16）：判断题位置发出
        #   字母会被服务端**整卷拒绝**（笼统回「作业提交失败！」），而不是只扣那题分。
        #   形状不符就留空 —— 留空会被接受，错形会把整份卷子打回。
        answers = sanitize_answers_by_type(answers, page_types)
        if not answers:
            raise ChaoxingError(f"测验 {work_id} 的答案与页面题型全不符（共 {len(questions)} 题）")

        # ★ 只答出一部分时**绝不交卷**：服务端会接受并记 0 分，任务点却显示完成。
        #   完整性以**表单 ID 集**为准，而不是“明文页解析出多少题”：
        #   form_ids 可能比 questions 多（明文页没返回的题），这些 ID 一个都不能漏。
        missing_ids = [qid for qid in form_ids if f"answer{qid}" not in answers]
        if self._settings.chaoxing.quiz_require_full_answers and missing_ids:
            self._acct_log(
                account_id,
                "quiz_incomplete",
                work_id=work_id,
                questions=len(questions),
                form_ids=len(form_ids),
                answered=len(answers),
                missing_ids=missing_ids,
                unidentified=len(unidentified),
            )
            raise ChaoxingError(
                f"测验 {work_id} 未完整作答：{len(answers)}/{len(form_ids)} 个表单题目"
                f"（缺 {len(missing_ids)} 个 answer，{len(unidentified)} 题无 ID），"
                "拒绝提交空白/部分答案"
            )

        try:
            envelope = await self._quiz_api.submit(
                session,
                work_id=work_id,
                url=final_url,
                answers=answers,
                hidden_fields=hidden_fields,
                question_ids=form_ids,
                user_id=str(payload.get("userid", "")),
                form_action=form_action,
                input_code=input_code,
            )
        except HumanRequired as exc:
            # 真机实测（2026-10-16）：服务端对某份答卷转入「要求人工验证码」态后，
            # 自动化到这一层必须有人读码（OCR 实测不可用：默认/beta/预处理三路皆乱码）。
            # 把图片落盘并报「需要人工」，别让它退化成与原因无关的「作业提交失败！」。
            image_path = await self._save_quiz_captcha(session, work_id)
            raise HumanRequired(
                f"测验 {work_id} 需要人工验证码（服务端：{exc}）；验证码已存到 {image_path}"
            ) from exc
        confidence = sum(confidences) / len(confidences) if confidences else 0.0
        await self._record_attempt(
            account_id=account_id,
            point=point,
            work_id=work_id,
            total=len(form_ids),
            answered=len(answers),
            reply=envelope.reason,
            confidence=confidence,
        )
        self._bus.publish(
            QuizAnswered(
                account_id=account_id,
                work_id=work_id,
                question_count=len(questions),
                answered=len(answers),
                submitted=envelope.ok,
                confidence=confidence,
            )
        )
        return "done" if envelope.ok else "failed"

    async def _record_attempt(
        self,
        *,
        account_id: int,
        point: PointView,
        work_id: str,
        total: int,
        answered: int,
        reply: str,
        confidence: float,
    ) -> None:
        """写一条刷题记录（``quiz_attempts`` 通道）。"""
        async with self._uow.begin() as db:
            db.add(
                QuizAttempt(
                    account_id=account_id,
                    course_id=None,
                    task_point_id=point.id or None,
                    work_id=work_id,
                    question_count=total,
                    answered_count=answered,
                    submitted=answered > 0,
                    reply=reply[:500],
                    detail={"confidence": confidence},
                )
            )
            await db.flush()

    async def _refresh_quiz_payload(self, account_id: int, point: PointView) -> dict[str, Any]:
        """测验执行前**就地刷新票据**（真机实测：扫描时存的 ``enc`` 几分钟就轮换）。

        用 :meth:`CourseService.refresh_quiz_tokens` 的新值**覆盖** payload 里的同名
        字段，其余字段原样保留；刷新拿不到东西（返回 ``{}``，或未注入课程服务）时
        返回旧 payload —— 失败要软，不许因为刷新不成功就让任务点直接失败。

        :param account_id: 账号 ID
        :param point: 任务点视图
        :returns: 供换票与提交使用的 payload（新值覆盖旧值、旧字段不丢）
        """
        payload = point.payload
        if self._courses is None:
            return payload
        fresh = await self._courses.refresh_quiz_tokens(
            account_id,
            course_id=str(payload.get("courseId", "")),
            clazz_id=str(payload.get("clazzId", "")),
            chapter_id=point.chapter_id or str(payload.get("knowledgeid", "")),
            work_id=str(payload.get("workid", "")),
        )
        if not fresh:
            return payload
        return {**payload, **fresh}

    async def _quiz_redirect(self, session: SessionState, point: PointView, work_id: str) -> str:
        """换票（独立方法便于测试替换）。"""
        payload = point.payload
        return await self._quiz_api.redirect_to_work(
            session,
            work_id=work_id,
            job_id=point.job_id,
            course_id=str(payload.get("courseId", "")),
            clazz_id=str(payload.get("clazzId", "")),
            cpi=str(payload.get("cpi", "")),
            knowledge_id=point.chapter_id,
            ktoken=str(payload.get("ktoken", "")),
            enc=str(payload.get("enc", "")),
            utenc=str(payload.get("utEnc", "")),
        )

    async def _save_quiz_captcha(self, session: SessionState, work_id: str) -> Path:
        """把作业验证码图片落盘，返回路径。

        沿用 ``cli/auth.py`` 的同一约定：人工处理类图片统一放 ``data/captcha/``。
        命名带 ``work_id`` 与时间戳，同一份答卷重试时不会互相覆盖。
        """
        from chaoxing.config import get_settings

        directory = get_settings().app.data_dir / "captcha"
        directory.mkdir(parents=True, exist_ok=True)
        image = await self._quiz_api.fetch_verify_captcha(session)
        path = directory / f"quiz-{work_id}-{int(time.time())}.jpg"
        path.write_bytes(image)
        return path

    # ------------------------------------------------------------------ #
    # 数据与状态
    # ------------------------------------------------------------------ #
    async def _load_points(
        self, account_id: int, course_ids: Sequence[int] | None
    ) -> list[PointView]:
        """取出待处理任务点（按 sequence 排序）。

        失败点会**再试一次**，但不会无限重试：`retry_count` 到 :data:`RETRY_LIMIT` 后不再自动
        选中（2026-10-12 真机实测：15 个被 403 拦下的测验每次 run 都重试，retry 涨到 3，
        全是白耗请求）。要重试就 `cx study reset` 打回 queued —— 那条路会把 retry_count 清零。
        """
        async with self._uow.read() as db:
            statement = select(TaskPoint).where(
                TaskPoint.account_id == account_id,
                or_(
                    TaskPoint.status.in_(("queued", "running")),
                    (TaskPoint.status == "failed") & (TaskPoint.retry_count < RETRY_LIMIT),
                ),
            )
            if course_ids:
                statement = statement.where(TaskPoint.course_id.in_(list(course_ids)))
            statement = statement.order_by(TaskPoint.sequence, TaskPoint.id)
            return [PointView.from_orm(row) for row in (await db.scalars(statement)).all()]

    async def _mark(
        self,
        point_id: int,
        status: str,
        *,
        progress: float | None = None,
        error: str = "",
        bump_retry: bool = False,
    ) -> None:
        """更新任务点状态（一个写事务）。"""
        async with self._uow.begin() as db:
            await TaskPointRepo(db).mark(
                point_id, status=status, progress=progress, error=error, bump_retry=bump_retry
            )

    def _session_for(self, account_id: int) -> SessionState:
        """取会话（没有登录态就明确报错）。"""
        if not self._registry.has(account_id):
            raise ChaoxingError(f"账号 {account_id} 没有登录态，先执行 `cx auth login`")
        return self._registry.get(account_id)

    async def get_point(self, point_id: int) -> PointView | None:
        """按主键取单个任务点的执行视图（诊断与 CLI 探针用）。

        :param point_id: 任务点主键
        :returns: :class:`PointView`；不存在时返回 ``None``
        """
        async with self._uow.read() as db:
            row = await db.get(TaskPoint, point_id)
            return PointView.from_orm(row) if row is not None else None

    async def run_quiz(self, account_id: int, point_id: int, *, input_code: str = "") -> str:
        """按任务点跑一次测验（验证码接力的入口）。

        真机实测（2026-10-16）：某份答卷被服务端转入「要求人工验证码」态时，
        ``_run_quiz`` 会落盘验证码图片并抛 :class:`~chaoxing.client.exceptions.HumanRequired`
        （消息里带图片路径）；人工读出四位码后**带 ``input_code`` 再调本方法**即完成交卷。

        :param account_id: 账号 ID
        :param point_id: 任务点主键
        :param input_code: 人工读出的验证码；不需要验证码时留空
        :returns: ``done`` / ``already_done`` / ``failed``（与 :meth:`_run_quiz` 一致）
        """
        point = await self.get_point(point_id)
        if point is None:
            raise ChaoxingError(f"任务点 {point_id} 不存在")
        self._bus.publish(
            TaskPointStarted(account_id=account_id, point_id=point.point_id, kind=point.kind)
        )
        started = time.monotonic()
        try:
            result = await self._run_quiz(account_id, point, input_code=input_code)
        except HumanRequired as exc:
            # 与 _dispatch 一致：需要人工的落 need_human（前端据此弹出验证码输入框）
            await self._mark(point_id, "need_human", error=str(exc))
            self._bus.publish(
                TaskPointFinished(
                    account_id=account_id,
                    point_id=point.point_id,
                    kind=point.kind,
                    status="need_human",
                    detail=str(exc),
                    elapsed_s=time.monotonic() - started,
                )
            )
            raise
        normalized = "done" if result in {"done", "already_done"} else result
        await self._mark(
            point_id,
            normalized,
            error="" if normalized == "done" else result,
        )
        # ★ 必须发 TaskPointFinished：`ActivityObserver` 靠它累加按天统计（仪表盘
        #   "测验完成"/"待刷"的数据源）。少了它，走本入口交的卷子会**只在任务点表里
        #   变 done、仪表盘永远不动**（真机 2026-10-17：62 份新交卷全被漏计）。
        self._bus.publish(
            TaskPointFinished(
                account_id=account_id,
                point_id=point.point_id,
                kind=point.kind,
                status=normalized,
                detail="" if normalized == "done" else result,
                elapsed_s=time.monotonic() - started,
            )
        )
        return result

    async def status(self, account_id: int) -> dict[str, int]:
        """任务点状态统计（``cx study status`` 用）。"""
        async with self._uow.read() as db:
            return await TaskPointRepo(db).status_counts(account_id)

    async def list_points(self, course_id: int, *, limit: int = 200) -> list[TaskPoint]:
        """按播放顺序列出某门课的任务点（网页端课程详情页用）。

        :param course_id: 课程 ID
        :param limit: 上限（防止一门课几千个点时把响应撑爆）
        :returns: 任务点列表
        """
        async with self._uow.read() as db:
            statement = (
                select(TaskPoint)
                .where(TaskPoint.course_id == course_id)
                .order_by(TaskPoint.sequence, TaskPoint.id)
                .limit(max(1, min(limit, 2000)))
            )
            return list((await db.scalars(statement)).all())
