"""课程域路由。"""

from __future__ import annotations

from fastapi import APIRouter

from chaoxing.api.routers.course.routes import router as course_router

router = APIRouter()
router.include_router(course_router)

__all__ = ["router"]
