"""题库域路由。"""

from __future__ import annotations

from fastapi import APIRouter

from chaoxing.api.routers.quiz.attempts import router as attempts_router
from chaoxing.api.routers.quiz.routes import router as quiz_router

router = APIRouter()
router.include_router(quiz_router)
router.include_router(attempts_router)

__all__ = ["router"]
