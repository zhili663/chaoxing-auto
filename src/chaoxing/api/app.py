"""FastAPI 应用工厂。

三处硬约束体现在这个文件里：

* **容器随进程生死**：lifespan 里建、退出时关，路由通过 ``request.app.state.container``
  取用 —— 与 CLI 共用同一份装配（限速、事件、审计口径完全一致）；
* **错误形状统一**：业务错误、校验错误、未预期异常都走 :mod:`chaoxing.api.errors`
  的同一套载荷；
* **workers=1**：进程内事件总线 + SQLite 单写者，多 worker 会破坏这两条前提
  （见 ``docs/REFACTOR_PLAN.md`` §10 风险 4）。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from chaoxing.api.errors import install_error_handlers
from chaoxing.api.middleware import AuditMiddleware
from chaoxing.api.static_files import mount_frontend
from chaoxing.config import Settings, get_settings
from chaoxing.core.container import Container
from chaoxing.obs.events import EventBus
from chaoxing.obs.logging import setup_logging

log = logging.getLogger(__name__)

API_PREFIX = "/api"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """随进程建立/销毁容器。"""
    settings: Settings = app.state.settings
    setup_logging(settings.app.log_level, log_dir=settings.log_dir, json_output=True)
    container = await Container.create(settings)
    app.state.container = container
    log.info("API 已就绪（数据库 %s）", settings.db_path)
    try:
        yield
    finally:
        await container.aclose()
        log.info("API 已关闭")


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建应用。

    :param settings: 配置（默认取进程单例；测试可注入）
    :returns: :class:`FastAPI`
    """
    resolved = settings or get_settings()
    app = FastAPI(
        title="超星自动刷课",
        description="学习通自动刷课后端 API",
        version="2.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = resolved

    # 开发期前端在 vite 的端口上，需要放行；生产同源部署时这段不生效
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.app.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    install_error_handlers(app)
    # 审计中间件在最外层：连 403 与 500 也要留下痕迹（那正是最需要回溯的）
    app.add_middleware(AuditMiddleware)
    _install_routers(app)
    # SPA fallback 必须**最后**挂：它通吃非 API 路径，先挂会把上面的路由全遮住
    mount_frontend(app, resolved.web_dist)
    return app


def _install_routers(app: FastAPI) -> None:
    """挂载各域路由（**全部**在 ``/api`` 前缀下）。"""
    from chaoxing.api.routers import (
        accounts,
        audit,
        auth,
        course,
        groups,
        logs,
        meta,
        quiz,
        settings,
        stats,
        stream,
        study,
        system,
        users,
    )

    app.include_router(system.router, prefix=API_PREFIX)
    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(users.router, prefix=API_PREFIX)
    app.include_router(accounts.router, prefix=API_PREFIX)
    app.include_router(course.router, prefix=API_PREFIX)
    app.include_router(study.router, prefix=API_PREFIX)
    app.include_router(quiz.router, prefix=API_PREFIX)
    app.include_router(stream.router, prefix=API_PREFIX)
    app.include_router(settings.router, prefix=API_PREFIX)
    app.include_router(audit.router, prefix=API_PREFIX)
    app.include_router(groups.router, prefix=API_PREFIX)
    app.include_router(meta.router, prefix=API_PREFIX)
    app.include_router(stats.router, prefix=API_PREFIX)
    app.include_router(logs.router, prefix=API_PREFIX)


def event_bus_of(app: FastAPI) -> EventBus:
    """取应用容器里的事件总线（SSE 与调试接口用）。"""
    container: Container = app.state.container
    return container.bus


__all__ = ["API_PREFIX", "create_app", "event_bus_of", "lifespan"]
