"""账号域：超星账号与虚拟账户组。

设计要点（``docs/REFACTOR_PLAN.md`` §5.8）：

* **不强制绑定**"平台用户 ↔ 超星账号" —— ``owner_user_id`` 可空，
  管理员批量导入的账号可以先没有属主，需要归属时再绑；
* **虚拟账户组**是批量操作与共享的粒度（"机房 A 的 30 个号"），组本身也可无属主（系统组）；
* 凭据一律加密落库（``password_enc`` / ``cookie_enc`` 由 Fernet 处理），表里没有明文位置。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column

from chaoxing.storage.base import Base, TimestampMixin

#: 组 ↔ 账号
account_group_members = Table(
    "account_group_members",
    Base.metadata,
    Column("group_id", ForeignKey("account_groups.id", ondelete="CASCADE"), primary_key=True),
    Column("account_id", ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True),
)


class Account(Base, TimestampMixin):
    """一个超星账号。

    :cvar owner_user_id: 属主（**可空** = 未绑定 / 管理员共享）
    :cvar session_state: 最近一次会话快照（cookie / 令牌），供启动恢复
    """

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, default=None
    )
    username: Mapped[str] = mapped_column(String(64), index=True)
    #: Fernet 加密后的密码（可逆 —— 代登录必须能取回明文）
    password_enc: Mapped[str] = mapped_column(Text, default="")
    #: Fernet 加密后的 cookie 串
    cookie_enc: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: 刷课顺序（数值小的先刷）
    priority: Mapped[int] = mapped_column(default=100, nullable=False)
    note: Mapped[str] = mapped_column(String(255), default="")
    #: 会话快照（cookie / 令牌 / Referer），由 SessionRegistry 节流写入
    session_state: Mapped[dict[str, Any]] = mapped_column(default=dict)
    #: 登录态关键字（pending / logged_in / expired / captcha / error）
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class AccountGroup(Base, TimestampMixin):
    """虚拟账户组：批量启停、批量建计划、共享可见性的粒度。"""

    __tablename__ = "account_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    #: 可空 = 系统组（不属于任何个人）
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, default=None
    )
    #: public = 组内账号对成员可见；private = 仅属主可见
    visibility: Mapped[str] = mapped_column(String(16), default="private", nullable=False)
    note: Mapped[str] = mapped_column(String(255), default="")
