"""审计域路由。"""

from __future__ import annotations

from fastapi import APIRouter

from chaoxing.api.routers.audit.routes import router as audit_router

router = APIRouter()
router.include_router(audit_router)

__all__ = ["router"]
