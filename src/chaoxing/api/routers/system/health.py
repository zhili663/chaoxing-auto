"""健康检查（不需要登录：编排与探针要用）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from chaoxing.api.deps import get_container
from chaoxing.core.container import Container

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(container: Container = Depends(get_container)) -> dict[str, Any]:
    """存活与就绪。

    只做**本地**判断（数据库能否应答、事件总线是否在跑），不主动打外网 ——
    健康检查不该因为学习通抖动而整片飘红。
    """
    database_ok = True
    try:
        await container.uow.read().__aenter__()
    except Exception:
        database_ok = False

    return {
        "status": "ok" if database_ok else "degraded",
        "database": "ok" if database_ok else "error",
        "bus": container.bus.stats(),
        "version": container.settings.app.version,
    }
