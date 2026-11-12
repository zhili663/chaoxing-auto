"""系统域：健康检查、运行概览、端到端体检、端点注册表、任务记录。"""

from __future__ import annotations

from fastapi import APIRouter

from chaoxing.api.routers.system.doctor import router as doctor_router
from chaoxing.api.routers.system.endpoints import router as endpoints_router
from chaoxing.api.routers.system.health import router as health_router
from chaoxing.api.routers.system.overview import router as overview_router
from chaoxing.api.routers.system.tasks import router as tasks_router

router = APIRouter()
router.include_router(health_router)
router.include_router(overview_router)
router.include_router(doctor_router)
router.include_router(endpoints_router)
router.include_router(tasks_router)

__all__ = ["router"]
