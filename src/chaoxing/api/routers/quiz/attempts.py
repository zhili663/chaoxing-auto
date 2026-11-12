"""刷题记录端点（`GET /quiz/attempts`）。

页面要回答的是"做过哪些测验、结果如何"，而 `quiz_attempts` 一次交卷一行 ——
所以这里只提供**只读**的按次列表，写路径没有（记录只能由刷课过程产生）。

数据范围走 `chaoxing.api.scoping` 的共用口径（study / plans / stats 同一份）：

* 普通用户必须指定**自己的**账号，不指定 → 400 `account_required`
  （否则"不传参数"等于把全库记录送出去）；
* 指定了别人的账号 → **404**（403 等于确认"这个账号存在/有记录"，
  等于把存在性变成可探测信息）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from chaoxing.api.deps import AuthContext, get_container, require
from chaoxing.api.scoping import scoped_account
from chaoxing.core.container import Container
from chaoxing.core.services.quiz_attempts import DEFAULT_LIMIT, AttemptView

router = APIRouter(tags=["quiz"])


class AttemptOut(BaseModel):
    """一条交卷记录。

    字段来自 ``storage/models/quiz.py`` 的 ``QuizAttempt``（含 ``created_at``），
    另加两个展示字段：``account_label``（脱敏账号）与 ``course_title``（可空 ——
    早先的记录没落课程，或课程已被删）。
    """

    id: int
    account_id: int
    account_label: str
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


def mask_username(username: str) -> str:
    """账号脱敏（与账号台账前端同一口径）。

    手机号留前 3 后 4，其余长账号留首尾两位 —— 记录页只需要"是哪个号交的"，
    没有理由把全号铺在列表里。
    """
    name = username.strip()
    if name.startswith("1") and len(name) == 11 and name.isdigit():
        return f"{name[:3]}****{name[7:]}"
    if len(name) <= 4:
        return name
    return f"{name[:2]}****{name[-2:]}"


def to_attempt_out(row: AttemptView) -> AttemptOut:
    """查询视图 → 响应模型。"""
    return AttemptOut(
        id=row.id,
        account_id=row.account_id,
        account_label=mask_username(row.account_username),
        course_id=row.course_id,
        course_title=row.course_title,
        task_point_id=row.task_point_id,
        work_id=row.work_id,
        question_count=row.question_count,
        answered_count=row.answered_count,
        submitted=row.submitted,
        reply=row.reply,
        detail=row.detail,
        created_at=row.created_at,
    )


@router.get("/quiz/attempts")
async def list_attempts(
    account_id: int | None = None,
    course_id: int | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    ctx: AuthContext = Depends(require("quiz:read")),
    container: Container = Depends(get_container),
) -> list[AttemptOut]:
    """列出交卷记录（交卷时间倒序；`limit` 夹在 1~200，超出静默夹紧）。"""
    scope = await scoped_account(account_id, ctx, container)
    rows = await container.quiz_attempts.list_attempts(
        account_id=scope, course_id=course_id, limit=limit, offset=offset
    )
    return [to_attempt_out(row) for row in rows]
