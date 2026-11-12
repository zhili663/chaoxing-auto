"""KPI 总览（仪表盘顶部四块）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from chaoxing.api.deps import AuthContext, get_container, require
from chaoxing.api.scoping import scoped_account
from chaoxing.core.container import Container

router = APIRouter(tags=["stats"])


@router.get("/stats/overview")
async def overview(
    account_id: int | None = None,
    ctx: AuthContext = Depends(require("study:record:read")),
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    """KPI + 今日活动 + 可见账号数（管理员可不指定账号看全局）。"""
    scope = await scoped_account(account_id, ctx, container)
    return await container.stats.overview(
        account_id=scope, owner_user_id=None if ctx.sees_everything else ctx.user.id
    )
