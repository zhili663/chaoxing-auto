"""课程与任务点域。

``task_points.payload`` 是关键设计：把卡片透传的字段（``otherInfo`` / ``jtoken`` /
``attDurationEnc`` …）**原样存下来**。心跳与提交都在原始 payload 上"只覆盖进度与答案字段"，
因此即使某天学习通改了字段名，只要它出现在卡片里就仍然能提交 —— 这条策略来自 legacy 的
实测经验，是任务点模块抗改版的核心。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from chaoxing.storage.base import Base, TimestampMixin


class Course(Base, TimestampMixin):
    """一门课（学习通按"课程 + 班级"定位，``course_key`` 即二者的组合键）。"""

    __tablename__ = "courses"
    __table_args__ = (
        UniqueConstraint("account_id", "course_key", name="uq_courses_account_course"),
        Index("ix_courses_account_selected", "account_id", "selected"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    #: ``"{courseId}:{clazzId}"``
    course_key: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    teacher: Mapped[str] = mapped_column(String(128), default="")
    cover: Mapped[str] = mapped_column(String(512), default="")
    #: 是否勾选要刷（**由网页 CRUD 决定，代码里不得有默认值**）
    selected: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)
    priority: Mapped[int] = mapped_column(default=100, nullable=False)
    note: Mapped[str] = mapped_column(String(255), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    #: 软删（保留历史统计，列表默认过滤）
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Chapter(Base, TimestampMixin):
    """章节（学习通叫"知识点"）。"""

    __tablename__ = "chapters"
    __table_args__ = (UniqueConstraint("course_id", "chapter_id", name="uq_chapters_course_ch"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    chapter_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    #: 章序号（用来推导任务点的全局播放顺序）
    position: Mapped[int] = mapped_column(default=0, nullable=False)


class TaskPoint(Base, TimestampMixin):
    """一个任务点（视频 / 测验 / 考试 / 文档 / 讨论）。"""

    __tablename__ = "task_points"
    __table_args__ = (
        UniqueConstraint("course_id", "point_id", name="uq_task_points_course_point"),
        Index("ix_task_points_account_status", "account_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    chapter_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    #: 视频是 objectId，测验是卡片里的 workid
    point_id: Mapped[str] = mapped_column(String(128), index=True)
    #: 学习通的任务点 ID（提交与心跳都要）
    job_id: Mapped[str] = mapped_column(String(64), default="")
    #: video / quiz / exam / document / read / discussion / unknown
    kind: Mapped[str] = mapped_column(String(16), default="unknown", index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    #: queued / running / done / failed / skipped / need_human
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    #: 已上报的进度（秒）
    progress: Mapped[float] = mapped_column(default=0.0, nullable=False)
    #: 总时长（秒）
    duration: Mapped[float] = mapped_column(default=0.0, nullable=False)
    #: 全局播放顺序（``章序号 × 1000 + 章内序号``，决定"视频后紧跟的题"）
    sequence: Mapped[int] = mapped_column(default=0, index=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    retry_count: Mapped[int] = mapped_column(default=0, nullable=False)
    #: 卡片透传字段（心跳 / 提交时原样回传，只覆盖进度与答案）
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
