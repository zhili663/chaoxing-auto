"""用户与角色域路由。"""

from __future__ import annotations

from fastapi import APIRouter

from chaoxing.api.routers.users.manage import router as manage_router
from chaoxing.api.routers.users.routes import router as users_router

router = APIRouter()
router.include_router(users_router)
router.include_router(manage_router)

__all__ = ["router"]
