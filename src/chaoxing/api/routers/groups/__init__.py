"""账户组域路由。"""

from __future__ import annotations

from fastapi import APIRouter

from chaoxing.api.routers.groups.routes import router as groups_router

router = APIRouter()
router.include_router(groups_router)

__all__ = ["router"]
