"""刷课控制面：暂停 / 继续 / 中止信号、检查点、进度重置与事件回看。

设计（计划 §P3 的关键决策）：

* 信号存 **数据库**（``study_runs``）而不是进程内存 —— CLI 与 API 是两个进程，
  调度器跑在 API 进程里，内存信号传不过去；
* 运行器只在**任务点边界与每次心跳**各读一次（一条 SQL），不是轮询定时器；
* ``paused`` **不阻塞睡等**：检查点把控制权交回调用方，由它决定"本次到此为止，
  下次 resume 继续"。睡等会一直占着会话与连接，而且进程一重启状态就没了。

状态机（非法转移直接报错，绝不静默改状态）::

    running ──pause──▶ pause_requested ──检查点──▶ paused
    paused  ──resume─▶ resume_requested ──检查点──▶ running（清控制行）
    pause_requested ──resume──▶ running（取消尚未生效的暂停请求）
    running / pause_requested / resume_requested ──stop──▶ stop_requested
                                                    ──检查点──▶ running（收尾并清行）

"清控制行"与"运行中"是同一件事：没有控制行就是没有信号，所以 :meth:`StudyControlService.current`
对没有控制行的账号也返回 ``running``，而不是报错。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from chaoxing.storage.base import utcnow
from chaoxing.storage.models import (
    RUN_PAUSE_REQUESTED,
    RUN_PAUSED,
    RUN_RESUME_REQUESTED,
    RUN_RUNNING,
    RUN_STOP_REQUESTED,
    StudyRun,
    TaskEvent,
    TaskPoint,
)
from chaoxing.storage.uow import UnitOfWork

#: 事件回看一次最多返回多少条（面板是给人扫的，不是归档）
MAX_EVENTS = 500

#: 检查点结论：继续 / 暂停 / 中止
CHECK_CONTINUE = "continue"
CHECK_PAUSE = "pause"
CHECK_STOP = "stop"

#: 每个状态的中文说明（**唯一**一份：CLI、API、网页都用它，不再各写一套映射）
STATE_NOTES: dict[str, str] = {
    RUN_RUNNING: "运行中（没有控制信号）",
    RUN_PAUSE_REQUESTED: "已请求暂停，等运行器走到检查点",
    RUN_PAUSED: "已暂停：运行器已在检查点交出控制权，本次到此为止（等继续）",
    RUN_RESUME_REQUESTED: "已请求继续，等运行器走到检查点",
    RUN_STOP_REQUESTED: "已请求中止：运行器将在检查点提前收尾（已完成进度保留）",
}

#: 请求动作 → 中文动词（错误信息里用）
_VERBS: dict[str, str] = {
    RUN_PAUSE_REQUESTED: "暂停",
    RUN_RESUME_REQUESTED: "继续",
    RUN_STOP_REQUESTED: "中止",
}

#: "当前状态 → 请求后的状态"；``None`` = 清掉控制行（等价运行中）。
#: 表里没有的组合就是非法转移（例如"没有暂停却要继续"），报错而不是猜。
_ALLOWED: dict[str, dict[str, str | None]] = {
    RUN_PAUSE_REQUESTED: {
        RUN_RUNNING: RUN_PAUSE_REQUESTED,
        RUN_PAUSE_REQUESTED: RUN_PAUSE_REQUESTED,  # 幂等：连点两次暂停不该报错
        RUN_RESUME_REQUESTED: RUN_PAUSE_REQUESTED,  # 改主意：继续请求被暂停请求取代
        RUN_PAUSED: RUN_PAUSED,  # 已经是暂停态，原样返回
    },
    RUN_RESUME_REQUESTED: {
        RUN_PAUSED: RUN_RESUME_REQUESTED,
        RUN_PAUSE_REQUESTED: None,  # 暂停还没生效 → 取消它，回到运行中
        RUN_RESUME_REQUESTED: RUN_RESUME_REQUESTED,
    },
    RUN_STOP_REQUESTED: {
        RUN_RUNNING: RUN_STOP_REQUESTED,
        RUN_PAUSE_REQUESTED: RUN_STOP_REQUESTED,
        RUN_RESUME_REQUESTED: RUN_STOP_REQUESTED,
        RUN_STOP_REQUESTED: RUN_STOP_REQUESTED,
    },
}


class InvalidTransition(Exception):
    """不允许的状态转移（例如在没有暂停的情况下要"继续"）。"""


class RunInterrupted(Exception):
    """运行器在检查点按要求提前收尾（视频心跳中途也抛它）。"""

    def __init__(self, verdict: Checkpoint) -> None:
        super().__init__(verdict.note)
        self.verdict = verdict


@dataclass(frozen=True, slots=True)
class RunState:
    """控制面对外的一行（API / CLI / 网页共用同一形状）。"""

    account_id: int
    state: str
    requested_at: datetime | None = None
    updated_at: datetime | None = None
    note: str = ""


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """一次检查点的结论。"""

    #: ``continue`` / ``pause`` / ``stop``
    action: str
    #: 结论所依据的控制状态（写进报告，便于回答"为什么停了"）
    state: str
    #: 给人看的原因
    note: str


def _view(account_id: int, row: StudyRun | None) -> RunState:
    """ORM 行 → 对外视图（没有行 = 运行中）。"""
    if row is None:
        return RunState(account_id=account_id, state=RUN_RUNNING, note=STATE_NOTES[RUN_RUNNING])
    return RunState(
        account_id=account_id,
        state=row.state,
        requested_at=row.requested_at,
        updated_at=row.updated_at,
        note=row.note or STATE_NOTES.get(row.state, ""),
    )


class StudyControlService:
    """控制信号与检查点。

    只碰三张表：``study_runs``（信号）、``task_points``（重置）、``task_events``（回看）。

    :param uow: 工作单元
    """

    def __init__(self, *, uow: UnitOfWork) -> None:
        self._uow = uow

    # ------------------------------------------------------------------ #
    # 写侧：请求 / 清除
    # ------------------------------------------------------------------ #
    async def request(self, account_id: int, state: str) -> RunState:
        """写入一个控制请求（幂等请求不算错，非法转移报错）。

        :param account_id: 账号 ID
        :param state: ``pause_requested`` / ``resume_requested`` / ``stop_requested``
        :raises InvalidTransition: 不可请求的状态，或当前状态不允许该转移
        :returns: 写入后的控制状态
        """
        if state not in _ALLOWED:
            raise InvalidTransition(f"不可请求的控制状态：{state}")

        async with self._uow.begin() as db:
            row = await self._row(db, account_id)
            current = row.state if row is not None else RUN_RUNNING
            transitions = _ALLOWED[state]
            if current not in transitions:
                raise InvalidTransition(
                    f"当前状态 {current}（{STATE_NOTES.get(current, '')}）不能{_VERBS[state]}"
                )
            target = transitions[current]
            if target is None:
                if row is not None:
                    await db.delete(row)
                # 这一支只有"暂停还没生效就按继续"：说明是**撤销**，不是普通的运行中
                return RunState(
                    account_id=account_id,
                    state=RUN_RUNNING,
                    note="已取消尚未生效的暂停请求，任务可继续推进",
                )
            if row is None:
                row = StudyRun(
                    account_id=account_id,
                    state=target,
                    requested_at=utcnow(),
                    note=STATE_NOTES[target],
                )
                db.add(row)
            elif row.state != target:
                row.state = target
                row.requested_at = utcnow()
                row.note = STATE_NOTES[target]
            await db.flush()
            return _view(account_id, row)

    async def clear(self, account_id: int) -> None:
        """清除控制信号（等价于回到运行中）。"""
        async with self._uow.begin() as db:
            row = await self._row(db, account_id)
            if row is not None:
                await db.delete(row)

    # ------------------------------------------------------------------ #
    # 读侧
    # ------------------------------------------------------------------ #
    async def current(self, account_id: int) -> RunState:
        """当前控制状态（没有控制行 = ``running``）。"""
        async with self._uow.read() as db:
            return _view(account_id, await self._row(db, account_id))

    async def states(self, account_ids: Sequence[int]) -> dict[int, RunState]:
        """一次查出多个账号的控制状态（列表页用；缺行 = ``running``）。

        :param account_ids: 账号 ID 列表
        :returns: ``{account_id: RunState}``
        """
        unique = list(dict.fromkeys(account_ids))
        rows: dict[int, StudyRun] = {}
        if unique:
            async with self._uow.read() as db:
                statement = select(StudyRun).where(StudyRun.account_id.in_(unique))
                rows = {row.account_id: row for row in (await db.scalars(statement)).all()}
        return {item: _view(item, rows.get(item)) for item in unique}

    async def checkpoint(self, account_id: int) -> Checkpoint:
        """读一次信号并给出结论（运行器在任务点边界与每次心跳调用）。

        只有需要**改**状态的路径才开写事务：最常见的"没有信号"只读一行 ——
        心跳每 12~20 秒一次，没必要每次都抢写锁。

        :param account_id: 账号 ID
        :returns: :class:`Checkpoint`
        """
        state, note = await self._signal(account_id)

        if state == RUN_PAUSE_REQUESTED:
            return await self._confirm_pause(account_id)
        if state == RUN_STOP_REQUESTED:
            await self.clear(account_id)
            return Checkpoint(CHECK_STOP, state, note or STATE_NOTES[state])
        if state == RUN_RESUME_REQUESTED:
            await self.clear(account_id)
            return Checkpoint(CHECK_CONTINUE, state, "已收到继续请求，继续执行")
        if state == RUN_PAUSED:
            return Checkpoint(CHECK_PAUSE, RUN_PAUSED, note or STATE_NOTES[RUN_PAUSED])
        return Checkpoint(CHECK_CONTINUE, RUN_RUNNING, note)

    async def _signal(self, account_id: int) -> tuple[str, str]:
        """读当前信号（状态, 说明）；没有控制行时是"运行中"。"""
        async with self._uow.read() as db:
            row = await self._row(db, account_id)
            return (row.state, row.note) if row is not None else (RUN_RUNNING, "")

    async def _confirm_pause(self, account_id: int) -> Checkpoint:
        """把 "pause_requested" 落实成 "paused"（写事务里再确认一次状态）。"""
        async with self._uow.begin() as db:
            row = await self._row(db, account_id)
            if row is None:
                return Checkpoint(CHECK_CONTINUE, RUN_RUNNING, "")
            if row.state != RUN_PAUSE_REQUESTED:
                # 读与写之间用户改了主意（比如 resume）→ 以库里的最新状态为准，下一拍再判
                return Checkpoint(CHECK_CONTINUE, row.state, row.note)
            row.state = RUN_PAUSED
            row.note = STATE_NOTES[RUN_PAUSED]
            await db.flush()
        return Checkpoint(CHECK_PAUSE, RUN_PAUSED, STATE_NOTES[RUN_PAUSED])

    # ------------------------------------------------------------------ #
    # 进度重置与事件回看（CLI `reset` / `logs` 的数据来源）
    # ------------------------------------------------------------------ #
    async def reset_progress(self, account_id: int, *, course_id: int | None = None) -> int:
        """把任务点打回 ``queued``，**不动课程 / 账号 / 记录**。

        只改状态是不够的：``progress`` 停在时长上时，下次 run 会"一个心跳就通过"，
        等于假刷完 —— 所以进度、错误与重试计数一起清零。控制信号也一并清掉，
        否则残留的 ``paused`` 会让下一次 run 在第一个检查点就退出。

        :param account_id: 账号 ID
        :param course_id: 只重置某门课（``None`` = 该账号全部任务点）
        :returns: 被打回的任务点数量
        """
        async with self._uow.begin() as db:
            statement = update(TaskPoint).where(TaskPoint.account_id == account_id)
            if course_id is not None:
                statement = statement.where(TaskPoint.course_id == course_id)
            result = await db.execute(
                statement.values(status="queued", progress=0.0, last_error="", retry_count=0)
            )
            row = await self._row(db, account_id)
            if row is not None:
                await db.delete(row)
            return int(getattr(result, "rowcount", 0) or 0)

    async def recent_events(
        self, account_id: int, *, course_id: int | None = None, limit: int = 50
    ) -> list[TaskEvent]:
        """倒序读任务点事件流水（最新在前）。

        时间戳可能同秒（同一轮里有多条），因此再按主键倒序，保证"最新在前"稳定。

        :param account_id: 账号 ID
        :param course_id: 只读某门课（``None`` = 不限）
        :param limit: 条数上限（压到 1..:data:`MAX_EVENTS`）
        :returns: 事件行
        """
        size = max(1, min(int(limit), MAX_EVENTS))
        async with self._uow.read() as db:
            statement = select(TaskEvent).where(TaskEvent.account_id == account_id)
            if course_id is not None:
                statement = statement.where(TaskEvent.course_id == course_id)
            statement = statement.order_by(TaskEvent.created_at.desc(), TaskEvent.id.desc()).limit(
                size
            )
            return list((await db.scalars(statement)).all())

    @staticmethod
    async def _row(db: AsyncSession, account_id: int) -> StudyRun | None:
        """按账号取控制行（``account_id`` 唯一）。"""
        statement = select(StudyRun).where(StudyRun.account_id == account_id)
        return (await db.scalars(statement)).first()
