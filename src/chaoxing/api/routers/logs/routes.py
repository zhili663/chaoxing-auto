"""应用日志读取（`GET /logs`）。

解析与过滤在 ``obs/log_reader.py``（日志格式是基建的属性），这里只做鉴权与参数裁剪。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from chaoxing.api.deps import AuthContext, get_container, require
from chaoxing.core.container import Container
from chaoxing.obs.log_reader import read_recent

router = APIRouter(tags=["logs"])

#: 单次最多返回多少条（防止有人拿 `?lines=100000` 把日志全拖走）
MAX_LINES = 500


@router.get("/logs")
async def read_logs(
    lines: int = 200,
    min_level: str = "INFO",
    ctx: AuthContext = Depends(require("system:logs")),
    container: Container = Depends(get_container),
) -> list[dict[str, Any]]:
    """读 `app.log` 尾部（按 `min_level` 过滤）。"""
    return read_recent(
        container.settings.log_dir / "app.log",
        lines=max(1, min(lines, MAX_LINES)),
        min_level=min_level,
    )
