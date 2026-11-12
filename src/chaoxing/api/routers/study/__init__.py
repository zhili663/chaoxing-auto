"""刷课域路由（计划 CRUD / 执行 / 控制 / 状态 / 统计读侧）。"""

from __future__ import annotations

from fastapi import APIRouter

from chaoxing.api.routers.study.captcha import router as captcha_router
from chaoxing.api.routers.study.control import router as control_router
from chaoxing.api.routers.study.plans import router as plans_router
from chaoxing.api.routers.study.routes import router as study_router

router = APIRouter()
router.include_router(plans_router)
router.include_router(control_router)
router.include_router(study_router)
router.include_router(captcha_router)

__all__ = ["router"]
