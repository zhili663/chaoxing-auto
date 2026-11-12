"""统计与刷课控制端点。

统计是**只读**的（写入口在刷课流程里，靠事件累加），权限只需 ``study:record:read``；
执行与状态控制则要 ``study:plan:run`` / ``study:plan:control``。

数据可见性在这里有一条硬规则：**普通用户必须指定自己的账号**，
不指定就直接拒绝 —— 否则"不传参数"等于把全库合计送给他。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from chaoxing.api.deps import AuthContext, get_container, load_account, require, visible_owner
from chaoxing.api.errors import ApiError
from chaoxing.api.security import require_same_origin
from chaoxing.core.container import Container
from chaoxing.core.services.courses import NotLoggedIn

router = APIRouter(tags=["study"])


class RunRequest(BaseModel):
    """触发刷课。"""

    account_id: int = Field(ge=1)
    course_ids: list[int] = Field(default_factory=list, description="限定课程（空=全部）")
    limit: int | None = Field(default=None, ge=1, description="最多执行多少任务点（试跑）")


class RunResult(BaseModel):
    """刷课结果摘要。"""

    account_id: int
    units: int
    done: int
    failed: int
    blocked: int
    skipped: int
    #: 是否按要求提前收尾（0/1）：暂停或中止都会是 1，原因在 ``abort_reason`` / ``details``
    aborted: int = 0
    abort_reason: str = ""
    elapsed_s: float
    details: list[str] = Field(default_factory=list)


class ActiveAccountOut(BaseModel):
    """一个正在刷课的账号。"""

    account_id: int
    username: str
    #: 当前任务点标题；账号刚启动/正在等下一个任务点时为空
    point_title: str = ""
    kind: str = ""
    progress: float = 0.0
    duration: float = 0.0
    started_at: datetime | None = None
    updated_at: datetime | None = None


class ActiveRunsOut(BaseModel):
    """并发刷课总览。"""

    #: 并发模式常开；这里返回当前配置并发上限
    concurrency: int
    accounts: list[ActiveAccountOut]


class DailyPoint(BaseModel):
    """一天的活动。"""

    day: str
    points_done: int
    quiz_done: int
    watched_minutes: float
    failed_count: int


async def _scoped_account(
    account_id: int | None, ctx: AuthContext, container: Container
) -> int | None:
    """把请求里的账号范围收敛成"调用者有权看的那个"。

    :raises ApiError: 普通用户没指定账号
    :raises NotFound: 指定了不属于自己的账号（与不存在同样处理）
    :returns: ``None`` = 全局（仅管理员可达）
    """
    if account_id is None:
        if ctx.sees_everything:
            return None
        raise ApiError("account_required", "请指定账号（普通用户只能查看自己的数据）")
    await load_account(account_id, ctx, container)
    return account_id


@router.get("/study/active")
async def active_study(
    ctx: AuthContext = Depends(require("study:record:read")),
    container: Container = Depends(get_container),
) -> ActiveRunsOut:
    """当前正在刷课的账号（并发模式常开；仪表盘模块的数据源）。"""
    runs = await container.study.active_accounts(owner_user_id=visible_owner(ctx))
    return ActiveRunsOut(
        concurrency=container.settings.chaoxing.max_account_concurrency,
        accounts=[
            ActiveAccountOut(
                account_id=item.account_id,
                username=item.username,
                point_title=item.point_title,
                kind=item.kind,
                progress=item.progress,
                duration=item.duration,
                started_at=item.started_at,
                updated_at=item.updated_at,
            )
            for item in runs
        ],
    )


@router.get("/stats/kpi")
async def kpi(
    account_id: int | None = None,
    ctx: AuthContext = Depends(require("study:record:read")),
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    """KPI（管理员可不指定账号看全局）。"""
    scope = await _scoped_account(account_id, ctx, container)
    return await container.stats.kpi(account_id=scope)


@router.get("/stats/daily")
async def daily(
    account_id: int | None = None,
    days: int = 14,
    ctx: AuthContext = Depends(require("study:record:read")),
    container: Container = Depends(get_container),
) -> list[DailyPoint]:
    """按天活动（热力图的数据源）。"""
    scope = await _scoped_account(account_id, ctx, container)
    series = await container.stats.daily(account_id=scope, days=max(1, min(days, 365)))
    return [
        DailyPoint(
            day=item.day,
            points_done=item.points_done,
            quiz_done=item.quiz_done,
            watched_minutes=item.watched_minutes,
            failed_count=item.failed_count,
        )
        for item in series
    ]


def to_run_result(report: object) -> RunResult:
    """把刷课报告转成响应模型（details 截断，避免一次响应几十 KB）。"""
    return RunResult(
        account_id=report.account_id,  # type: ignore[attr-defined]
        units=report.units,  # type: ignore[attr-defined]
        done=report.done,  # type: ignore[attr-defined]
        failed=report.failed,  # type: ignore[attr-defined]
        blocked=report.blocked,  # type: ignore[attr-defined]
        skipped=report.skipped,  # type: ignore[attr-defined]
        aborted=report.aborted,  # type: ignore[attr-defined]
        abort_reason=report.abort_reason,  # type: ignore[attr-defined]
        elapsed_s=round(report.elapsed_s, 2),  # type: ignore[attr-defined]
        details=report.details[:50],  # type: ignore[attr-defined]
    )


@router.post("/study/run")
async def run_study(
    payload: RunRequest,
    request: Request,
    ctx: AuthContext = Depends(require("study:plan:run")),
    container: Container = Depends(get_container),
) -> RunResult:
    """刷一个账号（同步执行；大批量请用 CLI）。"""
    require_same_origin(request)
    await load_account(payload.account_id, ctx, container)

    try:
        report = await container.study.run_account(
            payload.account_id, course_ids=payload.course_ids or None, limit=payload.limit
        )
    except NotLoggedIn as exc:
        raise ApiError("not_logged_in", str(exc)) from exc
    return to_run_result(report)


@router.get("/study/status")
async def study_status(
    account_id: int,
    ctx: AuthContext = Depends(require("study:record:read")),
    container: Container = Depends(get_container),
) -> dict[str, int]:
    """任务点状态分布。"""
    await load_account(account_id, ctx, container)
    return await container.study.status(account_id)
