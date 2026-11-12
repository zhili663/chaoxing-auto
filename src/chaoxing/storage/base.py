"""ORM 基类与通用约定。

三件事在这里定死，避免每个模型各写一套：

* **命名约定**：约束名由 SQLAlchemy 统一生成 —— 否则 Alembic 自动生成的迁移里
  会出现匿名约束，日后想删改索引只能手写 SQL；
* **时间戳**：统一 UTC + 时区感知（``utils`` 层负责展示时再转本地）；
* **JSON 列**：SQLite 用 ``JSON`` 类型承载结构化字段（策略、快照、会话状态）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

#: 约束命名模板（Alembic 的 autogenerate 依赖它产出稳定的名字）
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """当前 UTC 时间（**带时区**，避免幼稚 datetime 落库后无法比较）。"""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    # dict[str, Any] 直接映射成 JSON 列（SQLite 原生支持 JSON 文本）。
    # 这必须是类属性（SQLAlchemy 的注解解析依赖它），因此显式豁免"可变类属性"规则。
    type_annotation_map = {dict[str, Any]: JSON}  # noqa: RUF012


class TimestampMixin:
    """给模型加 ``created_at`` / ``updated_at``。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
