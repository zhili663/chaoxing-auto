"""设置域路由。"""

from __future__ import annotations

from fastapi import APIRouter

from chaoxing.api.routers.settings.routes import router as settings_router

router = APIRouter()
router.include_router(settings_router)

__all__ = ["router"]
