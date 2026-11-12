"""运行概览：KPI、事件总线、题库规模。

``system:doctor`` 权限节点守着 —— 这些数字里有全库口径的信息，
不该对普通用户开放（数据可见性不只看"能不能调接口"）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from chaoxing.api.deps import AuthContext, get_container, require
from chaoxing.core.container import Container

router = APIRouter(tags=["system"])


@router.get("/system/overview")
async def overview(
    ctx: AuthContext = Depends(require("system:doctor")),
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    """管理员视角的运行概览。

    普通用户拿不到这个接口；他们看自己的数据走 ``/stats/kpi``（``account:read``）。
    """
    kpi = await container.stats.kpi()
    return {
        "kpi": kpi,
        "bus": container.bus.stats(),
        "answers": await container.answers.stats(),
        "accounts": len(await container.accounts.list_accounts()),
        "database": str(container.settings.db_path),
    }
