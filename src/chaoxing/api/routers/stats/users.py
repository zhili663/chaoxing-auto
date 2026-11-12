"""按属主聚合（跨账号视图：只有持 ``account:read:all`` 的人能看）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from chaoxing.api.deps import AuthContext, get_container, require
from chaoxing.core.container import Container

router = APIRouter(tags=["stats"])


@router.get("/stats/users")
async def users(
    ctx: AuthContext = Depends(require("account:read:all")),
    container: Container = Depends(get_container),
) -> list[dict[str, Any]]:
    """每个属主一行（``owner_user_id=None`` = 共享账号）。"""
    rows = await container.stats.by_user()
    return [
        {
            "owner_user_id": row.owner_user_id,
            "accounts": row.accounts,
            "points_done": row.points_done,
        }
        for row in rows
    ]
