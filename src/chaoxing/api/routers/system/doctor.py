"""系统自省：端到端体检（与 `cx doctor --full` 共用同一套探针）。

为什么值得有个端点：排障时"服务在跑但不确定装配对不对"是最常见的状态，
让它在浏览器/curl 里一条命令就能看，比翻 journalctl 快。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from chaoxing.api.deps import AuthContext, get_container, require
from chaoxing.cli.doctor import probe_app
from chaoxing.core.container import Container

router = APIRouter(tags=["system"])


@router.get("/system/doctor")
async def doctor(
    ctx: AuthContext = Depends(require("system:doctor")),
    container: Container = Depends(get_container),
) -> list[dict[str, str]]:
    """跑一遍端到端探针（容器装配 / 健康检查 / SPA 托管 / SSE 端点）。"""
    results = await probe_app(container.settings)
    return [
        {"name": item.name, "status": str(item.status), "detail": item.detail} for item in results
    ]
