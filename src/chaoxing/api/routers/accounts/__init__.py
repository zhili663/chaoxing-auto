"""账号域路由。"""

from __future__ import annotations

from fastapi import APIRouter

from chaoxing.api.routers.accounts.routes import router as accounts_router

router = APIRouter()
router.include_router(accounts_router)

__all__ = ["router"]
