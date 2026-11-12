"""元数据端点：前端下拉 / 菜单 / 权限树的**唯一来源**（§5.10 第三条红线）。

前端不许硬编码枚举、权限节点、配置项 —— 后端加一个节点或一种类型，前端不发版也该能显示。
这些数据属于"我是谁"级别：**只要求登录**，不要求具体权限节点（与 ``/auth/me`` 同级）。

枚举一律**从代码常量派生**（``study.ALL_POINT_KINDS`` / ``DEFAULT_STRATEGY`` /
``QuestionType``），不在这里手抄第二份 —— 手抄的那份一定会漂。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from chaoxing.api.deps import get_container, require_user
from chaoxing.client.model.quiz import QuestionType
from chaoxing.core.container import Container
from chaoxing.core.services.accounts import ACCOUNT_STATUSES
from chaoxing.core.services.plans import DEFAULT_STRATEGY
from chaoxing.core.services.study import ALL_POINT_KINDS, TASK_STATUSES
from chaoxing.storage.models import Permission, User

router = APIRouter(prefix="/meta", tags=["meta"])


class MetaEnums(BaseModel):
    """前端需要的全部枚举。"""

    point_kinds: list[str]
    task_statuses: list[str]
    account_statuses: list[str]
    question_types: list[str]
    strategy_keys: list[str]


class PermissionNode(BaseModel):
    """一个权限节点（``group`` = 冒号前的前缀，前端据此分组渲染）。"""

    code: str
    group: str


@router.get("/enums")
async def enums(_user: User = Depends(require_user)) -> MetaEnums:
    """枚举集合（下拉 / 标签的唯一来源）。"""
    return MetaEnums(
        point_kinds=list(ALL_POINT_KINDS),
        task_statuses=list(TASK_STATUSES),
        account_statuses=list(ACCOUNT_STATUSES),
        question_types=[item.value for item in QuestionType],
        strategy_keys=sorted(DEFAULT_STRATEGY),
    )


@router.get("/point-kinds")
async def point_kinds(_user: User = Depends(require_user)) -> list[str]:
    """任务点类型。"""
    return list(ALL_POINT_KINDS)


@router.get("/permissions")
async def permissions(
    _user: User = Depends(require_user),
    container: Container = Depends(get_container),
) -> list[PermissionNode]:
    """权限节点树（按前缀分组）。"""
    async with container.uow.read() as db:
        codes = list((await db.scalars(select(Permission.code).order_by(Permission.code))).all())
    return [PermissionNode(code=code, group=code.split(":")[0]) for code in codes]
