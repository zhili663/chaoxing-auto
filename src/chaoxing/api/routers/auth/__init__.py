"""认证域路由：注册（含 nonce）、登录/登出、当前用户。"""

from __future__ import annotations

from fastapi import APIRouter

from chaoxing.api.routers.auth.register import router as register_router
from chaoxing.api.routers.auth.session import router as session_router

router = APIRouter()
router.include_router(register_router)
router.include_router(session_router)

__all__ = ["router"]
