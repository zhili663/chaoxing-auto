"""端点注册表查询（协议校准的读侧）。

数据源是 `client/endpoints` 的注册表 —— 与 `cx dev probe endpoints` 同一份，
所以网页上看到的置信度永远和命令行一致。
"""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Depends

from chaoxing.api.deps import AuthContext, require
from chaoxing.client import endpoints as ep

router = APIRouter(tags=["system"])


@router.get("/system/endpoints")
async def endpoints(
    ctx: AuthContext = Depends(require("system:doctor")),
) -> list[dict[str, str]]:
    """全部已注册端点（路径 / 方法 / 置信度 / 备注首句）。"""
    return [
        {
            "name": spec.name,
            "method": spec.method,
            "path": urlparse(spec.url).path,
            "calibration": str(spec.calibration),
            "note": spec.note.split("。")[0][:120],
        }
        for spec in ep.REGISTRY.values()
    ]
