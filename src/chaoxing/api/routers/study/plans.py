"""刷课计划端点：计划（账号 + 课程集合 + 策略）的 CRUD。

计划是「刷什么」的**权威来源**（§5.11）—— 所以课程名或 ID 不得出现在代码里，
请求体只收 ``course_key``（``"{courseId}:{clazzId}"``），由 `PlanService` 解析成实体。

两条硬规则：

* 读写分开挂权限：`study:plan:read` / `study:plan:write`；
* **账号范围先过 `load_account`** —— 越权按 404 处理（403 等于确认资源存在）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field

from chaoxing.api.deps import AuthContext, get_container, load_account, require
from chaoxing.api.errors import ApiError, NotFound
from chaoxing.api.security import require_same_origin
from chaoxing.core.container import Container
from chaoxing.core.services.plans import PlanNotFound

router = APIRouter(prefix="/study/plans", tags=["study"])


class PlanCreate(BaseModel):
    """新建计划。"""

    name: str = Field(default="", max_length=128)
    account_id: int = Field(ge=1)
    course_keys: list[str] = Field(default_factory=list, description="空 = 该账号全部已勾选的课程")
    strategy: dict[str, Any] | None = Field(default=None, description="与默认策略合并")


class PlanPatch(BaseModel):
    """计划的局部更新（当前只允许启停）。"""

    enabled: bool


class PlanOut(BaseModel):
    """计划的响应模型。"""

    id: int
    name: str
    account_id: int
    enabled: bool
    strategy: dict[str, Any]
    course_ids: list[int]


def _to_out(plan: Any, course_ids: list[int]) -> PlanOut:
    """ORM 对象 → 响应模型。"""
    return PlanOut(
        id=plan.id,
        name=plan.name,
        account_id=plan.account_id,
        enabled=plan.enabled,
        strategy=dict(plan.strategy or {}),
        course_ids=course_ids,
    )


async def _scoped_account(
    account_id: int | None, ctx: AuthContext, container: Container
) -> int | None:
    """把请求里的账号范围收敛成"调用者有权看的那个"（与 `study/routes.py` 同口径）。

    :raises ApiError: 普通用户没指定账号
    :returns: ``None`` = 全局（仅管理员可达）
    """
    if account_id is None:
        if ctx.sees_everything:
            return None
        raise ApiError("account_required", "请指定账号（普通用户只能查看自己的数据）")
    await load_account(account_id, ctx, container)
    return account_id


async def _visible_plan(plan_id: int, ctx: AuthContext, container: Container) -> Any:
    """取计划并校验可见性；不可见与不存在同样处理。

    :raises NotFound: 计划不存在，或它挂在调用者看不见的账号下
    """
    try:
        plan = await container.plans.get(plan_id)
    except PlanNotFound as exc:
        raise NotFound("plan_not_found", f"计划 {plan_id} 不存在") from exc
    await load_account(plan.account_id, ctx, container)
    return plan


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_plan(
    payload: PlanCreate,
    request: Request,
    ctx: AuthContext = Depends(require("study:plan:write")),
    container: Container = Depends(get_container),
) -> PlanOut:
    """新建计划（`course_keys` 空 = 用该账号已勾选的课程）。"""
    require_same_origin(request)
    await load_account(payload.account_id, ctx, container)

    plan = await container.plans.create(
        account_id=payload.account_id,
        name=payload.name,
        course_keys=payload.course_keys or None,
        strategy=payload.strategy,
        user_id=ctx.user.id,
    )
    return _to_out(plan, await container.plans.course_ids(plan.id))


@router.get("")
async def list_plans(
    account_id: int | None = None,
    ctx: AuthContext = Depends(require("study:plan:read")),
    container: Container = Depends(get_container),
) -> list[PlanOut]:
    """列出计划（普通用户必须指定自己的账号）。"""
    scope = await _scoped_account(account_id, ctx, container)
    plans = await container.plans.list_plans(account_id=scope)
    return [_to_out(plan, await container.plans.course_ids(plan.id)) for plan in plans]


@router.get("/{plan_id}")
async def get_plan(
    plan_id: int,
    ctx: AuthContext = Depends(require("study:plan:read")),
    container: Container = Depends(get_container),
) -> PlanOut:
    """取单个计划（含课程集合）。"""
    plan = await _visible_plan(plan_id, ctx, container)
    return _to_out(plan, await container.plans.course_ids(plan.id))


@router.patch("/{plan_id}")
async def patch_plan(
    plan_id: int,
    payload: PlanPatch,
    request: Request,
    ctx: AuthContext = Depends(require("study:plan:write")),
    container: Container = Depends(get_container),
) -> PlanOut:
    """启用 / 停用计划。"""
    require_same_origin(request)
    await _visible_plan(plan_id, ctx, container)

    await container.plans.set_enabled(plan_id, enabled=payload.enabled)
    plan = await container.plans.get(plan_id)
    return _to_out(plan, await container.plans.course_ids(plan_id))


@router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan(
    plan_id: int,
    request: Request,
    ctx: AuthContext = Depends(require("study:plan:write")),
    container: Container = Depends(get_container),
) -> Response:
    """删除计划（课程关联由服务层显式清理）。"""
    require_same_origin(request)
    await _visible_plan(plan_id, ctx, container)

    await container.plans.delete(plan_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
