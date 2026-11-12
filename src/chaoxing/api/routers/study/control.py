"""刷课控制端点：暂停 / 继续 / 各账号运行状态。

三条口径与其它域一致：

* 读写分开挂权限 —— 暂停/继续要 ``study:plan:control``（operator 及以上），
  读状态只要 ``study:record:read``；
* 账号范围先过 ``load_account`` —— 越权按 **404** 处理（403 等于确认账号存在）；
* 非法转移是 **409** ``invalid_transition``：请求本身合法，只是当前状态不允许。

响应模型固定叫 ``StudyRunState``（字段与 ``study_control.RunState`` 一一对应）——
前端 `useStudyRuns` / `pauseStudy` / `resumeStudy` 都按这个名字取生成类型。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from chaoxing.api.deps import AuthContext, get_container, load_account, require, visible_owner
from chaoxing.api.errors import Conflict
from chaoxing.api.security import require_same_origin
from chaoxing.core.container import Container
from chaoxing.core.services.study_control import InvalidTransition, RunState
from chaoxing.storage.models import RUN_PAUSE_REQUESTED, RUN_RESUME_REQUESTED

router = APIRouter(prefix="/study", tags=["study"])


class ControlRequest(BaseModel):
    """暂停 / 继续的请求体。"""

    account_id: int = Field(ge=1)


class StudyRunState(BaseModel):
    """一个账号的刷课控制状态。"""

    account_id: int
    #: running / pause_requested / paused / resume_requested / stop_requested
    state: str
    requested_at: datetime | None = None
    updated_at: datetime | None = None
    #: 状态说明（中文，直接可展示）
    note: str = ""


def _to_state(item: RunState) -> StudyRunState:
    """服务层视图 → 响应模型。"""
    return StudyRunState(
        account_id=item.account_id,
        state=item.state,
        requested_at=item.requested_at,
        updated_at=item.updated_at,
        note=item.note,
    )


async def _request(container: Container, account_id: int, state: str) -> StudyRunState:
    """写一个控制请求；非法转移翻成 409。"""
    try:
        return _to_state(await container.study.control.request(account_id, state))
    except InvalidTransition as exc:
        raise Conflict("invalid_transition", str(exc)) from exc


async def _scope_ids(account_id: int | None, ctx: AuthContext, container: Container) -> list[int]:
    """算出这次要看的账号 ID（普通用户只看到自己名下的账号）。"""
    if account_id is not None:
        await load_account(account_id, ctx, container)
        return [account_id]
    accounts = await container.accounts.list_accounts(owner_user_id=visible_owner(ctx))
    return [item.id for item in accounts]


@router.post("/pause")
async def pause_study(
    payload: ControlRequest,
    request: Request,
    ctx: AuthContext = Depends(require("study:plan:control")),
    container: Container = Depends(get_container),
) -> StudyRunState:
    """请求暂停：运行器在检查点交出控制权，本次到此为止。"""
    require_same_origin(request)
    await load_account(payload.account_id, ctx, container)
    return await _request(container, payload.account_id, RUN_PAUSE_REQUESTED)


@router.post("/resume")
async def resume_study(
    payload: ControlRequest,
    request: Request,
    ctx: AuthContext = Depends(require("study:plan:control")),
    container: Container = Depends(get_container),
) -> StudyRunState:
    """请求继续（重新执行刷课即从断点接着跑）。"""
    require_same_origin(request)
    await load_account(payload.account_id, ctx, container)
    return await _request(container, payload.account_id, RUN_RESUME_REQUESTED)


@router.get("/runs")
async def list_runs(
    account_id: int | None = None,
    ctx: AuthContext = Depends(require("study:record:read")),
    container: Container = Depends(get_container),
) -> list[StudyRunState]:
    """各账号的运行状态（缺省 = 调用者能看到的全部账号）。"""
    ids = await _scope_ids(account_id, ctx, container)
    states = await container.study.control.states(ids)
    return [_to_state(states[item]) for item in ids]
