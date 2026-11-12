"""日志路由包（`/logs`）。"""

from __future__ import annotations

from fastapi import APIRouter

from chaoxing.api.routers.logs.routes import router as logs_router

router = APIRouter()
router.include_router(logs_router)

__all__ = ["router"]
