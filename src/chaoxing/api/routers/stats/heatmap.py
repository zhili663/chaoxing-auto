"""热力图数据源（按天活动，缺的日期已在服务层补 0）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from chaoxing.api.deps import AuthContext, get_container, require
from chaoxing.api.scoping import scoped_account
from chaoxing.core.container import Container

router = APIRouter(tags=["stats"])


@router.get("/stats/heatmap")
async def heatmap(
    account_id: int | None = None,
    weeks: int = 13,
    ctx: AuthContext = Depends(require("study:record:read")),
    container: Container = Depends(get_container),
) -> list[dict[str, Any]]:
    """最近 N 周的按天活动（默认 13 周 ≈ 一个季度）。"""
    scope = await scoped_account(account_id, ctx, container)
    days = max(1, min(weeks, 52)) * 7
    series = await container.stats.daily(account_id=scope, days=days)
    return [
        {
            "day": item.day,
            "points_done": item.points_done,
            "quiz_done": item.quiz_done,
            "watched_minutes": item.watched_minutes,
        }
        for item in series
    ]
