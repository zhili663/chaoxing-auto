"""统计域路由包（KPI / 热力图 / 按用户聚合）。"""

from __future__ import annotations

from fastapi import APIRouter

from chaoxing.api.routers.stats.heatmap import router as heatmap_router
from chaoxing.api.routers.stats.overview import router as overview_router
from chaoxing.api.routers.stats.users import router as users_router

router = APIRouter()
router.include_router(overview_router)
router.include_router(heatmap_router)
router.include_router(users_router)

__all__ = ["router"]
