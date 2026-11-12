"""刷课域：计划、计划包含的课程、会话、控制信号与任务点事件流水。

"刷什么"由 ``study_plans`` + ``study_plan_courses`` 决定 —— 这是**唯一的权威来源**
（零硬编码原则：代码里不得出现课程名或 ID 的默认值）。
任务点事件流水（``task_events``）是"刷课记录"通道的载体，与审计、日志各自独立。
``study_runs`` 是暂停/继续/中止的**控制信号**（跨进程，见 :class:`StudyRun`）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from chaoxing.storage.base import Base, TimestampMixin


class StudyPlan(Base, TimestampMixin):
    """一个刷课计划：账号 + 课程集合 + 策略。"""

    __tablename__ = "study_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    #: 归属用户（可空 = 管理员建的公共计划）
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, default=None
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: 策略（视频/测验/文档开关、倍速、顺序 …），由网页配置
    strategy: Mapped[dict[str, Any]] = mapped_column(default=dict)
    note: Mapped[str] = mapped_column(String(255), default="")


class StudyPlanCourse(Base):
    """计划 ↔ 课程（带执行顺序）。"""

    __tablename__ = "study_plan_courses"
    __table_args__ = (UniqueConstraint("plan_id", "course_id", name="uq_plan_courses"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("study_plans.id", ondelete="CASCADE"), index=True
    )
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(default=0, nullable=False)


class StudySession(Base, TimestampMixin):
    """一次刷课会话（一次 run 的起止与结果）。"""

    __tablename__ = "study_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("study_plans.id", ondelete="SET NULL"), index=True, default=None
    )
    #: running / finished / aborted
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    #: 结束时汇总（完成数 / 阻塞项 / 下一步建议）
    summary: Mapped[dict[str, Any]] = mapped_column(default=dict)


class TaskEvent(Base):
    """任务点事件流水（刷课记录通道）。"""

    __tablename__ = "task_events"
    __table_args__ = (Index("ix_task_events_account_created", "account_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    course_id: Mapped[int | None] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True, default=None
    )
    task_point_id: Mapped[int | None] = mapped_column(
        ForeignKey("task_points.id", ondelete="CASCADE"), index=True, default=None
    )
    kind: Mapped[str] = mapped_column(String(16), default="")
    #: 事件名（heartbeat / finished / failed / need_human …）
    event: Mapped[str] = mapped_column(String(32), default="", index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


#: 无控制信号：任务可自由推进（没有控制行时也是这个语义）
RUN_RUNNING = "running"
#: 外部要求暂停，运行器还没走到检查点
RUN_PAUSE_REQUESTED = "pause_requested"
#: 运行器已在检查点交出控制权，**本次到此为止**（等 resume）
RUN_PAUSED = "paused"
#: 外部要求继续，运行器还没走到检查点
RUN_RESUME_REQUESTED = "resume_requested"
#: 外部要求中止，运行器在检查点提前收尾（已完成进度保留）
RUN_STOP_REQUESTED = "stop_requested"

#: 控制状态的规范列表（与 ``StudyRun.state`` 的取值一致）
RUN_STATES: tuple[str, ...] = (
    RUN_RUNNING,
    RUN_PAUSE_REQUESTED,
    RUN_PAUSED,
    RUN_RESUME_REQUESTED,
    RUN_STOP_REQUESTED,
)


class StudyRun(Base, TimestampMixin):
    """一个账号的刷课控制信号（暂停 / 继续 / 中止）。

    为什么是**数据库行**而不是进程内存：CLI 与 API 是两个进程，调度器跑在 API 进程里，
    内存里的信号传不过去。运行器只在任务点边界与心跳各读一次（一条 SQL），
    所以这里不需要轮询定时器，也不存在"命令发不进去"的问题。

    状态语义见上面五个常量；流转规则（哪些转移合法）在
    :class:`~chaoxing.core.services.study_control.StudyControlService` 里，模型层只管存。

    ``account_id`` 唯一 —— 一个账号同时只能有一个控制信号，多行会让"现在是什么状态"
    变成模糊问题。``requested_at`` 是**这次请求**写入的时间，与 ``updated_at``
    （行更新时间）分开：前者回答"命令什么时候下的"，后者回答"行什么时候被读过/改过"。
    """

    __tablename__ = "study_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), unique=True, index=True
    )
    state: Mapped[str] = mapped_column(String(24), default=RUN_RUNNING, index=True)
    #: 请求写入时间
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    #: 状态的中文说明（按状态统一写入，前端直接展示，不各写一份映射）
    note: Mapped[str] = mapped_column(String(255), default="")
