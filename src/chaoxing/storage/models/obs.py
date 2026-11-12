"""可观测性域：行为审计、按天活动统计、异步作业。

分工（``docs/REFACTOR_PLAN.md`` §5.6 的四通道）：

* ``audit_logs`` —— 行为审计：**谁**在什么时候对**哪个资源**做了什么、结果如何、从哪来；
* ``activity_daily`` —— 按天聚合，热力图与 KPI 的数据源（SSE 增量维护）；
* ``jobs`` —— 手动触发的异步任务（触发即落库，进程被杀也能看出来当时在干什么）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from chaoxing.storage.base import Base, TimestampMixin, utcnow


class AuditLog(Base):
    """行为审计（只增不改）。"""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_created_action", "created_at", "action"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    #: 操作者（null = 系统 / 匿名）
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, default=None
    )
    #: 动作码，例如 ``account.create`` / ``study.plan.run``
    action: Mapped[str] = mapped_column(String(64), index=True)
    resource: Mapped[str] = mapped_column(String(32), default="")
    resource_id: Mapped[str] = mapped_column(String(64), default="")
    #: ok / denied / error
    result: Mapped[str] = mapped_column(String(16), default="ok", index=True)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    #: 串联同一次请求的日志与事件
    request_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class ActivityDaily(Base, TimestampMixin):
    """按"账号 × 天"的活动统计（热力图 / KPI）。"""

    __tablename__ = "activity_daily"
    __table_args__ = (UniqueConstraint("account_id", "day", name="uq_activity_account_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    #: ``YYYY-MM-DD``（本地时区的"天"，统计口径按人看的来）
    day: Mapped[str] = mapped_column(String(10), index=True)
    points_done: Mapped[int] = mapped_column(default=0, nullable=False)
    quiz_done: Mapped[int] = mapped_column(default=0, nullable=False)
    watched_seconds: Mapped[float] = mapped_column(default=0.0, nullable=False)
    failed_count: Mapped[int] = mapped_column(default=0, nullable=False)


class Job(Base, TimestampMixin):
    """手动触发的异步任务记录。"""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: 例如 ``study.run`` / ``course.scan``
    kind: Mapped[str] = mapped_column(String(32), index=True)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), index=True, default=None
    )
    #: queued / running / done / failed / cancelled
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    result: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
