"""审计查询端点（``audit_logs`` 通道的读侧）。

写侧在 :mod:`chaoxing.api.middleware`（每个写操作自动留痕），这里只管查。
权限用 ``system:audit`` —— 审计里有跨账号的操作轨迹，不该对普通用户开放。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from chaoxing.api.deps import AuthContext, get_container, require
from chaoxing.core.container import Container
from chaoxing.storage.models import AuditLog

router = APIRouter(tags=["audit"])


class AuditItem(BaseModel):
    """一条审计记录。"""

    id: int
    user_id: int | None
    action: str
    resource: str
    result: str
    ip: str
    request_id: str
    detail: dict[str, Any] = {}
    created_at: datetime


def to_audit_item(row: AuditLog) -> AuditItem:
    """审计行 → 响应模型。"""
    return AuditItem(
        id=row.id,
        user_id=row.user_id,
        action=row.action,
        resource=row.resource,
        result=row.result,
        ip=row.ip,
        request_id=row.request_id,
        detail=dict(row.detail or {}),
        created_at=row.created_at,
    )


@router.get("/audit")
async def list_audit(
    action: str | None = None,
    result: str | None = None,
    user_id: int | None = None,
    limit: int = 100,
    ctx: AuthContext = Depends(require("system:audit")),
    container: Container = Depends(get_container),
) -> list[AuditItem]:
    """查审计（按动作 / 结果 / 操作者过滤，时间倒序）。"""
    rows = await container.audit.list(action=action, result=result, user_id=user_id, limit=limit)
    return [to_audit_item(row) for row in rows]


@router.get("/audit/actions")
async def list_actions(
    ctx: AuthContext = Depends(require("system:audit")),
    container: Container = Depends(get_container),
) -> Sequence[str]:
    """出现过的动作码（前端渲染筛选下拉）。"""
    return await container.audit.actions()
