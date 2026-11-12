"""运行时配置覆盖（第三层，``docs/REFACTOR_PLAN.md`` §5.10）。

三层优先级
----------
::

    代码默认  →  环境变量（.env）  →  **数据库覆盖**

网页与 CLI 改的是第三层：写在库里，重启不丢，也不用登服务器改文件。

安全边界
--------
能覆盖的键走**白名单**，且只允许业务参数 —— 监听地址、数据库路径、``SECRET_KEY``
这类"改了会把自己锁在门外（甚至让旧密文永久解不开）"的项不在其中。
这条比"多给几个可调项"重要得多。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from chaoxing.config import Settings
from chaoxing.storage.models import SettingsOverride
from chaoxing.storage.uow import UnitOfWork

log = logging.getLogger(__name__)

#: 允许运行时覆盖的键 → 值类型（**白名单**）
ALLOWED_KEYS: dict[str, type] = {
    "chaoxing.video_rate": float,
    "chaoxing.heartbeat_interval_min": float,
    "chaoxing.heartbeat_interval_max": float,
    "chaoxing.max_progress_step": float,
    "chaoxing.video_concurrency": int,
    "chaoxing.request_min_interval": float,
    "chaoxing.max_retry": int,
    "chaoxing.max_account_concurrency": int,
    "chaoxing.timeout": float,
    "chaoxing.quiz_require_full_answers": bool,
    "chaoxing.proxy_rotate_requests": int,
    "chaoxing.proxy_rotate_seconds": float,
    "ai.model": str,
    "ai.base_url": str,
    "ai.timeout": float,
    "obs.log_level": str,
    "obs.sse_buffer": int,
}

#: 读配置时**必须脱敏**的键（只回显"有没有配"，不回显值本身）
SECRET_KEYS = ("secret_key", "api_token", "api_key", "password", "token")


class UnknownSetting(Exception):
    """不在白名单里的键（拒绝写入，而不是默默忽略）。"""


class InvalidValue(Exception):
    """值的类型不对。"""


def coerce(key: str, raw: Any) -> Any:
    """把值转成该键应有的类型。

    :param key: 配置键
    :param raw: 原始值（通常来自 JSON 或命令行字符串）
    :raises UnknownSetting: 键不在白名单
    :raises InvalidValue: 转不过去
    :returns: 转换后的值
    """
    expected = ALLOWED_KEYS.get(key)
    if expected is None:
        raise UnknownSetting(f"{key} 不允许运行时修改（可选：{', '.join(sorted(ALLOWED_KEYS))}）")
    if expected is bool:
        return str(raw).lower() in {"1", "true", "yes", "on"}
    try:
        return expected(raw)
    except (TypeError, ValueError) as exc:
        raise InvalidValue(f"{key} 需要 {expected.__name__}，收到 {raw!r}") from exc


def nest(flat: dict[str, Any]) -> dict[str, Any]:
    """把 ``{"chaoxing.video_rate": 2}`` 还原成嵌套字典（pydantic 要的形状）。"""
    nested: dict[str, Any] = {}
    for key, value in flat.items():
        head, _, tail = key.partition(".")
        if not tail:
            continue
        section = nested.setdefault(head, {})
        if isinstance(section, dict):
            section[tail] = value
    return nested


def mask(key: str, value: Any) -> Any:
    """敏感项脱敏（只回显"是否已设置"）。"""
    if any(marker in key.lower() for marker in SECRET_KEYS):
        return "（已设置）" if value else ""
    return value


class PreferenceService:
    """配置覆盖的读写与解析。

    :param uow: 工作单元
    :param base: 基础配置（环境变量与代码默认的合成结果）
    """

    def __init__(self, *, uow: UnitOfWork, base: Settings) -> None:
        self._uow = uow
        self._base = base

    async def overrides(self) -> dict[str, Any]:
        """读出全部覆盖项。"""
        async with self._uow.read() as session:
            rows = list((await session.scalars(select(SettingsOverride))).all())
        return {str(row.key): (row.value or {}).get("value") for row in rows}

    async def describe(self) -> dict[str, dict[str, Any]]:
        """列出白名单里的每个键：当前值、默认值、是否被覆盖。

        前端设置页直接用这个结构渲染，因此**脱敏在这里做**（一处做，人人受益）。
        """
        current = self._base.model_dump()
        overrides = await self.overrides()
        described: dict[str, dict[str, Any]] = {}

        for key in sorted(ALLOWED_KEYS):
            default = _dig(current, key)
            described[key] = {
                "value": mask(key, overrides.get(key, default)),
                "default": mask(key, default),
                "overridden": key in overrides,
                "type": ALLOWED_KEYS[key].__name__,
            }
        return described

    async def set(self, key: str, raw: Any, *, user_id: int | None = None) -> Any:
        """写入一条覆盖。

        :param key: 配置键（必须在白名单里）
        :param raw: 新值
        :param user_id: 操作者（进审计的由头）
        :raises UnknownSetting: 键不在白名单
        :raises InvalidValue: 值类型不对
        :returns: 转换后的值
        """
        value = coerce(key, raw)
        async with self._uow.begin() as session:
            row = (
                await session.scalars(select(SettingsOverride).where(SettingsOverride.key == key))
            ).first()
            if row is None:
                row = SettingsOverride(key=key, value={"value": value}, updated_by=user_id)
                session.add(row)
            else:
                row.value = {"value": value}
                row.updated_by = user_id
            await session.flush()
        log.info("配置覆盖已更新：%s = %r（操作者 %s）", key, value, user_id)
        return value

    async def unset(self, key: str) -> bool:
        """删掉一条覆盖（回到环境变量/默认值）。"""
        async with self._uow.begin() as session:
            row = (
                await session.scalars(select(SettingsOverride).where(SettingsOverride.key == key))
            ).first()
            if row is None:
                return False
            await session.delete(row)
            await session.flush()
        log.info("配置覆盖已删除：%s", key)
        return True

    async def resolve(self) -> Settings:
        """把覆盖应用到基础配置上，得到**当前生效**的 Settings。"""
        overrides = await self.overrides()
        if not overrides:
            return self._base
        merged = {**self._base.model_dump(), **nest(overrides)}
        try:
            return Settings.model_validate(merged)
        except Exception as exc:
            log.error("覆盖项无法应用（%s），回退到基础配置：%s", exc, overrides)
            return self._base


def _dig(payload: dict[str, Any], key: str) -> Any:
    """按 ``a.b`` 从嵌套字典里取值（取不到返回 ``None``）。"""
    node: Any = payload
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node
