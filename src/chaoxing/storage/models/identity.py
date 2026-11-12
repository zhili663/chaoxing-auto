"""身份域：用户、登录会话、角色与权限节点。

设计要点（``docs/REFACTOR_PLAN.md`` §5.8）：

* 口令只存 **Argon2id 哈希**（前端已做 sha256 预哈希），表里没有明文位置；
* 会话是不透明 token 的**哈希**（库被读走也无法直接冒用登录态）；
* 权限是**节点表**（``study:plan:run`` 这样的字符串），角色与用户通过关联表绑定 ——
  权限点可增删、可审计，不写死在代码里（"零硬编码"在身份域的落点）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column

from chaoxing.storage.base import Base, TimestampMixin

#: 用户 ↔ 角色
user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)

#: 角色 ↔ 权限节点
role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base, TimestampMixin):
    """平台用户。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    #: 手机号（可选的第二个登录标识）。**可空唯一**：没填是 ``NULL``（多个 NULL 不互相冲突），
    #: 绝不能用空串 —— 空串之间会撞唯一索引
    phone: Mapped[str | None] = mapped_column(String(20), unique=True, index=True, default=None)
    display_name: Mapped[str] = mapped_column(String(64), default="")
    #: Argon2id 哈希；前端送来的是 sha256(password)，这里存它的哈希
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: 首个注册用户自动成为超管（持有通配权限）
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class UserSession(Base):
    """登录会话（存 token 的哈希，可撤销、可审计）。"""

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Role(Base, TimestampMixin):
    """角色（权限节点的集合）。"""

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")


class Permission(Base):
    """权限节点（``domain:resource:action`` 形式）。"""

    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
