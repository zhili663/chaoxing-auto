"""刷题记录读侧（``quiz_attempts`` 表）。

表里的行是**交卷那一刻写下的既成事实**（``StudyService._record_attempt``），
所以这里只有一条读路径：按可见范围过滤、按交卷时间倒序分页。没有写路径 ——
"记录"该由刷课过程产生，网页端能改它只会让回溯失去意义。

数据可见性**不在这里判断**：服务层只做过滤，谁看得见由 API 层按权限决定
（与 ``AccountService.list_accounts`` 同一分工，CLI 与 API 共用一套过滤）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select

from chaoxing.storage.models import Account, Course, QuizAttempt
from chaoxing.storage.uow import UnitOfWork

#: 默认返回条数
DEFAULT_LIMIT = 100
#: 单次最多返回条数（挡住 `?limit=100000` 把整表拖走）
MAX_LIMIT = 200


@dataclass(slots=True)
class AttemptView:
    """一条刷题记录（附展示要用的账号名与课程名）。"""

    id: int
    account_id: int
    account_username: str
    course_id: int | None
    course_title: str | None
    task_point_id: int | None
    work_id: str
    question_count: int
    answered_count: int
    submitted: bool
    reply: str
    detail: dict[str, Any]
    created_at: datetime


def _to_view(attempt: QuizAttempt, *, username: str, course_title: str | None) -> AttemptView:
    """查询行 → :class:`AttemptView`。"""
    return AttemptView(
        id=attempt.id,
        account_id=attempt.account_id,
        account_username=username,
        course_id=attempt.course_id,
        course_title=course_title,
        task_point_id=attempt.task_point_id,
        work_id=attempt.work_id,
        question_count=attempt.question_count,
        answered_count=attempt.answered_count,
        submitted=attempt.submitted,
        reply=attempt.reply,
        detail=dict(attempt.detail or {}),
        created_at=attempt.created_at,
    )


class QuizAttemptService:
    """刷题记录的只读查询。

    :param uow: 工作单元
    """

    def __init__(self, *, uow: UnitOfWork) -> None:
        self._uow = uow

    async def list_attempts(
        self,
        *,
        account_id: int | None = None,
        course_id: int | None = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> list[AttemptView]:
        """按交卷时间倒序列出记录（同一时刻的按 ID 倒序，保证分页稳定）。

        :param account_id: 只看某账号（``None`` = 不限，管理员视角）
        :param course_id: 只看某课程（``None`` = 不限）；历史记录里的课程可能为空
        :param limit: 返回条数，**夹在 1~200**：超上限静默夹紧而不是报 422 ——
            参数表达的是"我想要多少"，不是用户必须改正的错误，与
            ``/quiz/cache``、``/system/tasks`` 的既有口径一致
        :param offset: 跳过条数（负数按 0 处理）
        :returns: 记录列表（新的在前）
        """
        statement = (
            select(QuizAttempt, Account.username, Course.title)
            .join(Account, Account.id == QuizAttempt.account_id)
            # 课程只用来补标题：历史记录不该因为课程被删（软删）就丢掉课程名
            .outerjoin(Course, Course.id == QuizAttempt.course_id)
        )
        if account_id is not None:
            statement = statement.where(QuizAttempt.account_id == account_id)
        if course_id is not None:
            statement = statement.where(QuizAttempt.course_id == course_id)
        statement = (
            statement.order_by(QuizAttempt.created_at.desc(), QuizAttempt.id.desc())
            .limit(max(1, min(limit, MAX_LIMIT)))
            .offset(max(0, offset))
        )

        async with self._uow.read() as db:
            rows = (await db.execute(statement)).all()
        return [
            _to_view(attempt, username=username, course_title=title)
            for attempt, username, title in rows
        ]


__all__ = ["DEFAULT_LIMIT", "MAX_LIMIT", "AttemptView", "QuizAttemptService"]
