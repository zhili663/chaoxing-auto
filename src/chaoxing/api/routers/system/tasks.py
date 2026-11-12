"""手动任务记录（`jobs` 表）：列表与取消。

取消的状态机在服务层（`JobService.cancel`）—— 路由只做取依赖与错误映射，
免得"编排逻辑堆在路由里"（`scripts/check_router_size.py` 会拦）。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from chaoxing.api.deps import AuthContext, get_container, require
from chaoxing.api.errors import ApiError, NotFound
from chaoxing.api.security import require_same_origin
from chaoxing.core.container import Container
from chaoxing.core.services.jobs import JobNotCancellable, JobNotFound, JobView

router = APIRouter(tags=["system"])


class TaskOut(BaseModel):
    """一条任务记录。"""

    id: int
    kind: str
    account_id: int | None
    status: str
    result: str
    started_at: datetime | None
    finished_at: datetime | None


def _to_out(row: JobView) -> TaskOut:
    """视图 → 响应模型。"""
    return TaskOut(
        id=row.id,
        kind=row.kind,
        account_id=row.account_id,
        status=row.status,
        result=row.result,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


@router.get("/system/tasks")
async def list_tasks(
    limit: int = 100,
    ctx: AuthContext = Depends(require("system:task")),
    container: Container = Depends(get_container),
) -> list[TaskOut]:
    """最近的任务（新的在前）。"""
    rows = await container.jobs.list_recent(limit=limit)
    return [_to_out(row) for row in rows]


@router.post("/system/tasks/{job_id}/cancel")
async def cancel_task(
    job_id: int,
    request: Request,
    ctx: AuthContext = Depends(require("system:task")),
    container: Container = Depends(get_container),
) -> TaskOut:
    """取消一个还在排队 / 运行的任务（其余状态一律 400）。"""
    require_same_origin(request)
    try:
        return _to_out(await container.jobs.cancel(job_id))
    except JobNotFound as exc:
        raise NotFound("job_not_found", str(exc)) from exc
    except JobNotCancellable as exc:
        raise ApiError("job_not_cancellable", str(exc)) from exc
