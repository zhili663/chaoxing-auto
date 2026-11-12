"""题库端点（刷题页的读侧与人工修正）。

分工：

* ``quiz:read`` —— 看题库（题干、答案、来源、命中次数）；
* ``quiz:cache:write`` —— 人工修正或删除一条答案。

人工修正的答案来源标记为 ``manual``，之后**永不被模型覆盖** ——
这是"机器答错、人改一次就永久生效"的落点。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from chaoxing.api.deps import AuthContext, get_container, require
from chaoxing.api.errors import NotFound
from chaoxing.api.security import require_same_origin
from chaoxing.core.container import Container
from chaoxing.storage.models import AnswerCache

router = APIRouter(tags=["quiz"])


class AnswerItem(BaseModel):
    """一条题库记录。"""

    fingerprint: str
    question: str
    answer: str
    source: str
    confidence: float
    hits: int


class AnswerUpdate(BaseModel):
    """人工修正。"""

    value: str = Field(min_length=1, max_length=2000)


def to_answer_item(row: AnswerCache) -> AnswerItem:
    """题库行 → 响应模型。"""
    return AnswerItem(
        fingerprint=row.question_hash,
        question=row.question,
        answer=row.answer,
        source=row.source,
        confidence=row.confidence,
        hits=row.hits,
    )


@router.get("/quiz/cache")
async def list_cache(
    keyword: str = "",
    source: str = "",
    limit: int = 100,
    ctx: AuthContext = Depends(require("quiz:read")),
    container: Container = Depends(get_container),
) -> list[AnswerItem]:
    """列出题库（命中次数降序，常考的排前面）。"""
    rows = await container.answers.list_cached(limit=limit, keyword=keyword, source=source)
    return [to_answer_item(row) for row in rows]


@router.get("/quiz/stats")
async def quiz_stats(
    ctx: AuthContext = Depends(require("quiz:read")),
    container: Container = Depends(get_container),
) -> dict[str, int]:
    """题库概览。"""
    return await container.answers.stats()


@router.patch("/quiz/cache/{fingerprint}")
async def correct_answer(
    fingerprint: str,
    payload: AnswerUpdate,
    request: Request,
    ctx: AuthContext = Depends(require("quiz:cache:write")),
    container: Container = Depends(get_container),
) -> AnswerItem:
    """人工修正一条答案（改完标为 manual，模型不再覆盖它）。"""
    require_same_origin(request)
    row = await container.answers.correct(fingerprint, payload.value)
    if row is None:
        raise NotFound("answer_not_found", "这条记录不存在")
    return to_answer_item(row)


@router.delete("/quiz/cache/{fingerprint}")
async def forget_answer(
    fingerprint: str,
    request: Request,
    ctx: AuthContext = Depends(require("quiz:cache:write")),
    container: Container = Depends(get_container),
) -> dict[str, bool]:
    """删除一条缓存（下次遇到这道题会重新问模型）。"""
    require_same_origin(request)
    return {"removed": await container.answers.forget(fingerprint)}
