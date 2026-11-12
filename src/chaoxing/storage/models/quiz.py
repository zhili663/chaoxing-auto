"""题库与答题记录域。

``answer_cache`` 是成本控制的关键：同一道题（按题干指纹）只问一次模型，
命中即省一次调用；``quiz_attempts`` 是"刷题记录"通道的载体，记录答案来源
（cache / model / websearch）与置信度，便于回溯"这个答案是哪儿来的"。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from chaoxing.storage.base import Base, TimestampMixin


class QuizAttempt(Base, TimestampMixin):
    """一次测验作答记录。"""

    __tablename__ = "quiz_attempts"
    __table_args__ = (Index("ix_quiz_attempts_account_created", "account_id", "created_at"),)

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
    #: 数字形式的作业 ID
    work_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    question_count: Mapped[int] = mapped_column(default=0, nullable=False)
    answered_count: Mapped[int] = mapped_column(default=0, nullable=False)
    submitted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: 服务端回执（成功 / 已批阅 / 参数错误 …）
    reply: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)


class AnswerCache(Base, TimestampMixin):
    """题干指纹 → 答案缓存。"""

    __tablename__ = "answer_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: 题干归一化后的哈希（sha1，见 utils.text）
    question_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    question: Mapped[str] = mapped_column(Text, default="")
    answer: Mapped[str] = mapped_column(Text, default="")
    #: cache / model / websearch —— 答案来源要能追溯
    source: Mapped[str] = mapped_column(String(16), default="model", index=True)
    confidence: Mapped[float] = mapped_column(default=0.0, nullable=False)
    hits: Mapped[int] = mapped_column(default=0, nullable=False)
