"""配置覆盖层与提示词（"零硬编码"在存储层的落点）。

* ``settings_overrides``：运行时改配置的**唯一**写入点 —— 三层来源的最后一层
  （代码默认 → env → 数据库覆盖，见 ``docs/REFACTOR_PLAN.md`` §5.10）；
* ``prompts``：提示词入库，改完即生效，历史版本进审计。

敏感值（API key 等）同样落在这里，但写入前加密、读取时脱敏，由服务层负责。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from chaoxing.storage.base import Base, TimestampMixin


class SettingsOverride(Base, TimestampMixin):
    """一条运行时配置覆盖。"""

    __tablename__ = "settings_overrides"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: 形如 ``chaoxing.video_rate``（与 SettingsService 的键一致）
    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    value: Mapped[dict[str, Any]] = mapped_column(default=dict)
    #: 谁改的（null = 命令行/系统）
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, default=None
    )


class Prompt(Base, TimestampMixin):
    """一条提示词（可热改）。"""

    __tablename__ = "prompts"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    #: 每次修改自增，便于"这轮答案用的是哪版提示词"
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    description: Mapped[str] = mapped_column(String(255), default="")
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, default=None
    )
