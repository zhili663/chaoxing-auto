"""设置端点：读当前生效配置、写覆盖、删覆盖。

能改什么由 `core.services.preferences.ALLOWED_KEYS` 的白名单决定 ——
路由层不重复这份清单，避免两处不一致（一处放宽就等于白名单失效）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from chaoxing.api.deps import AuthContext, get_container, require
from chaoxing.api.errors import ApiError, NotFound
from chaoxing.api.security import require_same_origin
from chaoxing.core.container import Container
from chaoxing.core.services.preferences import InvalidValue, UnknownSetting

router = APIRouter(tags=["settings"])


class SettingUpdate(BaseModel):
    """更新一条覆盖。"""

    value: Any = None


class SettingItem(BaseModel):
    """一个配置项的状态。"""

    value: Any = None
    default: Any = None
    overridden: bool = False
    type: str = "str"


@router.get("/settings")
async def list_settings(
    ctx: AuthContext = Depends(require("settings:read")),
    container: Container = Depends(get_container),
) -> dict[str, SettingItem]:
    """列出可调项：当前值、默认值、是否被覆盖（敏感项已脱敏）。"""
    described = await container.preferences.describe()
    return {key: SettingItem(**item) for key, item in described.items()}


async def _apply(container: Container, key: str, raw: Any, user_id: int) -> Any:
    """写覆盖并把两类异常翻成 HTTP 错误。"""
    try:
        return await container.preferences.set(key, raw, user_id=user_id)
    except UnknownSetting as exc:
        raise NotFound("unknown_setting", str(exc)) from exc
    except InvalidValue as exc:
        raise ApiError("invalid_value", str(exc)) from exc


@router.patch("/settings/{key:path}")
async def update_setting(
    key: str,
    payload: SettingUpdate,
    request: Request,
    ctx: AuthContext = Depends(require("settings:write")),
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    """写入一条覆盖（白名单外的键一律拒绝）。"""
    require_same_origin(request)
    value = await _apply(container, key, payload.value, ctx.user.id)
    return {"key": key, "value": value, "overridden": True}


@router.delete("/settings/{key:path}")
async def delete_setting(
    key: str,
    request: Request,
    ctx: AuthContext = Depends(require("settings:write")),
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    """删掉一条覆盖（回到环境变量 / 代码默认）。"""
    require_same_origin(request)
    removed = await container.preferences.unset(key)
    return {"key": key, "removed": removed}


# --------------------------------------------------------------------------- #
# 提示词（`prompts` 表）—— 改完即生效，不需要重启
# --------------------------------------------------------------------------- #


class PromptIn(BaseModel):
    """写入一条提示词。"""

    content: str = Field(min_length=1)
    description: str = ""


class PromptOut(BaseModel):
    """提示词状态（`version` 每次写入自增）。"""

    key: str
    content: str
    version: int
    description: str


@router.get("/settings/prompts")
async def list_prompts(
    ctx: AuthContext = Depends(require("settings:read")),
    container: Container = Depends(get_container),
) -> list[PromptOut]:
    """列出全部提示词（这是 `prompts` 表唯一的读取入口）。"""
    rows = await container.prompts.list_all()
    return [
        PromptOut(
            key=row.key,
            content=row.content,
            version=row.version,
            description=row.description,
        )
        for row in rows
    ]


@router.put("/settings/prompts/{key}")
async def put_prompt(
    key: str,
    payload: PromptIn,
    request: Request,
    ctx: AuthContext = Depends(require("prompt:write")),
    container: Container = Depends(get_container),
) -> PromptOut:
    """写入提示词（不存在则建，存在则版本 +1；写操作由审计中间件记录）。"""
    require_same_origin(request)
    row = await container.prompts.upsert(
        key, payload.content, user_id=ctx.user.id, description=payload.description
    )
    return PromptOut(
        key=row.key,
        content=row.content,
        version=row.version,
        description=row.description,
    )
