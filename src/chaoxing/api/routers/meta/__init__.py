"""元数据路由包（`/meta/*`：枚举、权限节点树）。"""

from __future__ import annotations

from fastapi import APIRouter

from chaoxing.api.routers.meta.routes import router as meta_router

router = APIRouter()
router.include_router(meta_router)

__all__ = ["router"]
