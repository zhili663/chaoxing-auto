"""用户与权限服务（``docs/REFACTOR_PLAN.md`` §5.8）。

三条规则写在这里，而不是散在路由里：

* **首个用户自动成为超管**（持通配权限 ``*``）—— 否则第一次部署没人能授权；
* **口令只以 Argon2id 形态落库**，里里外外拿不到明文，也拿不到前端哈希值本身；
* **会话是不透明 token 的哈希**：库里被读走也无法直接冒用登录态。
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chaoxing.storage.base import utcnow
from chaoxing.storage.models import (
    Permission,
    Role,
    User,
    UserSession,
    role_permissions,
    user_roles,
)
from chaoxing.storage.uow import UnitOfWork
from chaoxing.utils.security import (
    hash_secret,
    new_session_token,
    token_fingerprint,
    verify_secret,
)

log = logging.getLogger(__name__)

#: 超管的通配权限
WILDCARD = "*"

#: 只看不动的角色：能看自己账号的一切，不能改
VIEWER_PERMISSIONS: tuple[str, ...] = (
    "account:read",
    "course:read",
    "study:plan:read",
    "study:record:read",
    "quiz:read",
    "stream:subscribe",
)

#: 干活的人：账号、课程、计划、刷题
OPERATOR_PERMISSIONS: tuple[str, ...] = (
    "account:read",
    "account:write",
    "account:login",
    "group:read",
    "group:write",
    "course:read",
    "course:scan",
    "course:write",
    "course:select",
    "study:plan:read",
    "study:plan:write",
    "study:plan:run",
    "study:plan:control",
    "study:record:read",
    "quiz:read",
    "quiz:write",
    "quiz:submit",
    "quiz:cache:write",
    "settings:read",
    "stream:subscribe",
)

#: 只有平台管理员该有的面：用户、角色、设置、系统
ADMIN_ONLY_PERMISSIONS: tuple[str, ...] = (
    "account:read:all",
    "user:read",
    "user:write",
    "role:read",
    "role:manage",
    "settings:read",
    "settings:write",
    "prompt:write",
    "system:audit",
    "system:logs",
    "system:task",
    "system:doctor",
)

#: 路由挂了 `require`、但不绑给任何内置角色的节点（删账号是危险动作，只给显式授权的人）
EXTRA_PERMISSIONS: tuple[str, ...] = ("account:delete",)

#: 内置角色 → 权限节点（新装环境直接可用，管理员再按需微调）。
#:
#: `admin` **就是全量目录**：单管理员部署是常态，只给"平台管理面"会让管理员连自己的
#: 课程页、账号分组、刷课计划都点不动（2026-10-11 实测：`/groups` 403）。
#: 权限的收窄交给 `viewer` / `operator`，以及管理员事后的自定义微调。
BUILTIN_ROLES: dict[str, tuple[str, ...]] = {
    "viewer": VIEWER_PERMISSIONS,
    "operator": OPERATOR_PERMISSIONS,
    "admin": tuple(
        dict.fromkeys(
            VIEWER_PERMISSIONS + OPERATOR_PERMISSIONS + ADMIN_ONLY_PERMISSIONS + EXTRA_PERMISSIONS
        )
    ),
}

#: 权限目录全量（角色模板 ∪ 额外节点）—— 启动时按它自愈，见 `UserService.sync_catalog`
PERMISSION_CATALOG: tuple[str, ...] = tuple(
    dict.fromkeys(
        [code for codes in BUILTIN_ROLES.values() for code in codes] + list(EXTRA_PERMISSIONS)
    )
)


class UserExists(Exception):
    """用户名或手机号已被占用。"""


def _clean_phone(phone: str | None) -> str | None:
    """把手机号规整成"填了就是非空串、没填就是 ``None``"。

    空串会让 ``phone`` 的唯一索引互相冲突（第二个不填手机号的用户直接建不出来），
    所以这里统一把空白收敛成 ``NULL`` —— 与迁移里的可空唯一口径一致。
    """
    if phone is None:
        return None
    return phone.strip() or None


def _default_secret_from_phone(phone: str | None) -> str:
    """没传口令时的默认口令：手机号后 6 位的 ``sha256``（与前端口径一致）。

    口径（2026-10-13 用户要求）：管理员建普通用户时可以不填初始口令 ——
    默认就是"注册手机号后 6 位"；连手机号也没有就**明确报错**（不能凭空造口令）。
    管理员/超管不走这条默认：路由层对 ``admin`` 角色留空直接拒绝。

    :param phone: 已规整的手机号（``None`` = 没填）
    :raises ValueError: 没有手机号，或不足 6 位
    :returns: ``sha256(后6位)`` 的十六进制（与前端 ``sha256(password)`` 同口径）
    """
    if not phone:
        raise ValueError("未提供口令，且没有手机号可用于默认口令")
    digits = phone.strip()
    if len(digits) < 6:
        raise ValueError("手机号不足 6 位，无法作为默认口令")
    return hashlib.sha256(digits[-6:].encode()).hexdigest()


class UserService:
    """用户、角色、权限与会话。

    :param uow: 工作单元
    """

    def __init__(self, *, uow: UnitOfWork) -> None:
        self._uow = uow

    # ------------------------------------------------------------------ #
    # 用户
    # ------------------------------------------------------------------ #
    async def count(self) -> int:
        """用户总数（判断"是否第一个用户"）。"""
        async with self._uow.read() as session:
            return int((await session.scalar(select(func.count()).select_from(User))) or 0)

    async def is_first_user(self) -> bool:
        """当前库是否还没有任何用户。"""
        return await self.count() == 0

    async def create(
        self,
        *,
        username: str,
        client_secret: str | None = None,
        display_name: str = "",
        is_superuser: bool | None = None,
        phone: str | None = None,
    ) -> User:
        """创建用户。

        :param username: 登录名
        :param client_secret: 前端 ``sha256(password)``；**留空**且给了手机号时，
            默认口令取手机号后 6 位（见 :func:`_default_secret_from_phone`）
        :param display_name: 展示名
        :param is_superuser: 是否超管（``None`` = 首个用户自动是）
        :param phone: 手机号（可选；非空时不能被别的账号占用）
        :raises UserExists: 用户名或手机号已存在
        :raises ValueError: 口令不是前端哈希值，或留空又没有手机号可用
        :returns: 新建用户
        """
        clean_phone = _clean_phone(phone)
        hashed = hash_secret(client_secret or _default_secret_from_phone(clean_phone))
        async with self._uow.begin() as session:
            existing = (
                await session.scalars(select(User).where(User.username == username))
            ).first()
            if existing is not None:
                raise UserExists(f"用户名 {username} 已被占用")
            if clean_phone is not None:
                taken = (
                    await session.scalars(select(User).where(User.phone == clean_phone))
                ).first()
                if taken is not None:
                    raise UserExists(f"手机号 {clean_phone} 已被占用")
            total = int((await session.scalar(select(func.count()).select_from(User))) or 0)
            user = User(
                username=username,
                phone=clean_phone,
                display_name=display_name or username,
                password_hash=hashed,
                is_superuser=bool(is_superuser) if is_superuser is not None else total == 0,
            )
            session.add(user)
            await session.flush()
            log.info("已创建用户 %s（超管=%s）", username, user.is_superuser)
            return user

    async def get(self, user_id: int) -> User | None:
        """按 ID 取用户。"""
        async with self._uow.read() as session:
            return await session.get(User, user_id)

    async def list_users(self) -> Sequence[User]:
        """全部用户（按 ID）。"""
        async with self._uow.read() as session:
            return list((await session.scalars(select(User).order_by(User.id))).all())

    async def authenticate(self, identifier: str, client_secret: str) -> User | None:
        """校验口令。

        ``identifier`` 是"手机号或用户名"：**先按手机号查、再按用户名查** ——
        一个纯数字的用户名（学号）与手机号可能同时存在，手机号优先保证登录入口唯一；
        两者都不中才返回 ``None``（调用方对"不存在"与"口令错"给同样文案，不给撞库信号）。

        :param identifier: 手机号或用户名
        :param client_secret: 前端 ``sha256(password)``
        :returns: 通过则返回用户，否则 ``None``
        """
        async with self._uow.begin() as session:
            user = (await session.scalars(select(User).where(User.phone == identifier))).first()
            if user is None:
                user = (
                    await session.scalars(select(User).where(User.username == identifier))
                ).first()
            if user is None or not user.is_active:
                return None
            if not verify_secret(client_secret, user.password_hash):
                return None
            user.last_login_at = utcnow()
            await session.flush()
            return user

    async def set_phone(self, user_id: int, phone: str | None) -> bool:
        """改手机号（空值清除，非空时不能被别的账号占用）。

        :param user_id: 用户 ID
        :param phone: 手机号；``None``/空白 = 清除
        :raises UserExists: 手机号已被别的账号占用
        :returns: 是否改到（用户不存在时 ``False``）
        """
        clean_phone = _clean_phone(phone)
        async with self._uow.begin() as session:
            user = await session.get(User, user_id)
            if user is None:
                return False
            if clean_phone is not None:
                taken = (
                    await session.scalars(
                        select(User).where(User.phone == clean_phone, User.id != user_id)
                    )
                ).first()
                if taken is not None:
                    raise UserExists(f"手机号 {clean_phone} 已被占用")
            user.phone = clean_phone
            await session.flush()
            return True

    async def set_password(self, user_id: int, client_secret: str) -> bool:
        """改密（要求用当前的加密口径重算）。"""
        hashed = hash_secret(client_secret)
        async with self._uow.begin() as session:
            user = await session.get(User, user_id)
            if user is None:
                return False
            user.password_hash = hashed
            await session.flush()
            return True

    # ------------------------------------------------------------------ #
    # 权限
    # ------------------------------------------------------------------ #
    async def permissions(self, user: User) -> set[str]:
        """算出用户的权限节点集合（超管返回通配）。

        :param user: 用户
        :returns: 权限码集合
        """
        if user.is_superuser:
            return {WILDCARD}
        async with self._uow.read() as session:
            statement = (
                select(Permission.code)
                .select_from(user_roles)
                .join(role_permissions, role_permissions.c.role_id == user_roles.c.role_id)
                .join(Permission, Permission.id == role_permissions.c.permission_id)
                .where(user_roles.c.user_id == user.id)
            )
            return {str(code) for code in (await session.scalars(statement)).all()}

    async def sync_catalog(self) -> dict[str, int]:
        """把权限目录与内置角色对齐（幂等，**只加不删**）。

        为什么必须跑：`BUILTIN_ROLES` 会随功能增长，而存量库建得更早 ——
        新节点既不在 `permissions` 表里、也没绑给任何角色，于是"路由要求一个谁都没有的节点"，
        **所有人** 403（KPI、统计、日志页就是这样整页空掉的）。这里做一次自愈。
        管理员的既有自定义授权不会被收回。

        :returns: 本次新增数量 ``{"permissions": …, "roles": …, "grants": …}``
        """
        created = {"permissions": 0, "roles": 0, "grants": 0}
        async with self._uow.begin() as session:
            known = {
                str(code)
                for code in (
                    await session.scalars(
                        select(Permission.code).where(Permission.code.in_(PERMISSION_CATALOG))
                    )
                ).all()
            }
            for code in PERMISSION_CATALOG:
                if code in known:
                    continue
                session.add(Permission(code=code, description=code))
                known.add(code)
                created["permissions"] += 1
            await session.flush()

            by_code = {
                str(node.code): node
                for node in (
                    await session.scalars(
                        select(Permission).where(Permission.code.in_(PERMISSION_CATALOG))
                    )
                ).all()
            }
            for name, codes in BUILTIN_ROLES.items():
                role = (await session.scalars(select(Role).where(Role.name == name))).first()
                if role is None:
                    role = Role(name=name, description=f"内置角色 {name}")
                    session.add(role)
                    await session.flush()
                    created["roles"] += 1
                granted = {
                    int(node_id)
                    for node_id in (
                        await session.scalars(
                            select(role_permissions.c.permission_id).where(
                                role_permissions.c.role_id == role.id
                            )
                        )
                    ).all()
                }
                for code in codes:
                    node = by_code.get(code)
                    if node is None or node.id in granted:
                        continue
                    await session.execute(
                        role_permissions.insert().values(role_id=role.id, permission_id=node.id)
                    )
                    granted.add(node.id)
                    created["grants"] += 1
            await session.flush()

        if any(created.values()):
            log.info(
                "权限目录已同步：新增节点 %d 个、角色 %d 个、授权 %d 条",
                created["permissions"],
                created["roles"],
                created["grants"],
            )
        return created

    async def grant(self, user_id: int, role_name: str) -> bool:
        """给用户授予角色（角色不存在则按内置定义创建）。

        :param user_id: 用户 ID
        :param role_name: 角色名（viewer / operator / admin）
        :returns: 是否成功
        """
        async with self._uow.begin() as session:
            user = await session.get(User, user_id)
            if user is None:
                return False
            role = await self._ensure_role(session, role_name)
            existing = (
                await session.execute(
                    user_roles.select().where(
                        user_roles.c.user_id == user_id, user_roles.c.role_id == role.id
                    )
                )
            ).first()
            if existing is None:
                await session.execute(user_roles.insert().values(user_id=user_id, role_id=role.id))
            await session.flush()
            return True

    async def revoke(self, user_id: int, role_name: str) -> bool:
        """收回角色。"""
        async with self._uow.begin() as session:
            role = (await session.scalars(select(Role).where(Role.name == role_name))).first()
            if role is None:
                return False
            await session.execute(
                user_roles.delete().where(
                    user_roles.c.user_id == user_id, user_roles.c.role_id == role.id
                )
            )
            await session.flush()
            return True

    @staticmethod
    async def _ensure_role(session: AsyncSession, name: str) -> Role:
        """取角色；不存在则按内置定义创建（含权限节点）。"""
        role = (await session.scalars(select(Role).where(Role.name == name))).first()
        if role is not None:
            return role

        role = Role(name=name, description=f"内置角色 {name}")
        session.add(role)
        await session.flush()

        for code in BUILTIN_ROLES.get(name, ()):
            permission = (
                await session.scalars(select(Permission).where(Permission.code == code))
            ).first()
            if permission is None:
                permission = Permission(code=code, description=code)
                session.add(permission)
                await session.flush()
            await session.execute(
                role_permissions.insert().values(role_id=role.id, permission_id=permission.id)
            )
        return role

    # ------------------------------------------------------------------ #
    # 会话
    # ------------------------------------------------------------------ #
    async def open_session(self, user: User, *, ip: str = "", user_agent: str = "") -> str:
        """创建登录会话。

        :param user: 用户
        :param ip: 客户端 IP
        :param user_agent: UA
        :returns: **明文 token**（只在这一次返回，之后库里只有指纹）
        """
        token = new_session_token()
        async with self._uow.begin() as session:
            session.add(
                UserSession(
                    token_hash=token_fingerprint(token),
                    user_id=user.id,
                    ip=ip[:64],
                    user_agent=user_agent[:255],
                    created_at=utcnow(),
                )
            )
            await session.flush()
        return token

    async def resolve_session(self, token: str) -> User | None:
        """用 token 换用户（顺手更新 ``last_seen``）。

        :param token: 会话 token
        :returns: 用户；无效或已撤销时 ``None``
        """
        if not token:
            return None
        fingerprint = token_fingerprint(token)
        async with self._uow.begin() as session:
            record = (
                await session.scalars(
                    select(UserSession).where(UserSession.token_hash == fingerprint)
                )
            ).first()
            if record is None or record.revoked_at is not None:
                return None
            user = await session.get(User, record.user_id)
            if user is None or not user.is_active:
                return None
            record.last_seen_at = utcnow()
            await session.flush()
            return user

    async def close_session(self, token: str) -> bool:
        """撤销会话（登出）。"""
        if not token:
            return False
        fingerprint = token_fingerprint(token)
        async with self._uow.begin() as session:
            record = (
                await session.scalars(
                    select(UserSession).where(UserSession.token_hash == fingerprint)
                )
            ).first()
            if record is None:
                return False
            record.revoked_at = utcnow()
            await session.flush()
            return True

    async def close_all_sessions(self, user_id: int) -> int:
        """撤销某用户的全部会话（改密后调用）。

        :param user_id: 用户 ID
        :returns: 撤销数量
        """
        async with self._uow.begin() as session:
            records = list(
                (
                    await session.scalars(
                        select(UserSession).where(
                            UserSession.user_id == user_id,
                            UserSession.revoked_at.is_(None),
                        )
                    )
                ).all()
            )
            for record in records:
                record.revoked_at = utcnow()
            await session.flush()
            return len(records)

    async def update(
        self,
        user_id: int,
        *,
        is_active: bool | None = None,
        display_name: str | None = None,
    ) -> User | None:
        """改启用状态 / 展示名（``None`` = 这一项不动）。

        :param user_id: 用户 ID
        :param is_active: 是否启用
        :param display_name: 展示名（空串回落用户名，不允许展示名是空白）
        :returns: 更新后的用户；不存在时 ``None``
        """
        async with self._uow.begin() as session:
            user = await session.get(User, user_id)
            if user is None:
                return None
            if is_active is not None:
                user.is_active = is_active
            if display_name is not None:
                user.display_name = display_name.strip() or user.username
            await session.flush()
            return user

    async def delete(self, user_id: int) -> bool:
        """删除用户（会话随之级联删除，见外键 ON DELETE CASCADE）。

        :param user_id: 用户 ID
        :returns: 是否删掉
        """
        async with self._uow.begin() as session:
            user = await session.get(User, user_id)
            if user is None:
                return False
            await session.delete(user)
            await session.flush()
            return True

    async def list_sessions(self, user_id: int) -> Sequence[UserSession]:
        """某用户的全部会话（含已撤销，按创建时间倒序）。

        :param user_id: 用户 ID
        :returns: 会话列表（库里只有 token 指纹，**没有明文**）
        """
        async with self._uow.read() as session:
            rows = await session.scalars(
                select(UserSession)
                .where(UserSession.user_id == user_id)
                .order_by(UserSession.created_at.desc())
            )
            return list(rows.all())
