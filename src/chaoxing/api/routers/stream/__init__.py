"""实时通道路由。"""

from __future__ import annotations

from fastapi import APIRouter

from chaoxing.api.routers.stream.routes import router as stream_router

router = APIRouter()
router.include_router(stream_router)

__all__ = ["router"]
