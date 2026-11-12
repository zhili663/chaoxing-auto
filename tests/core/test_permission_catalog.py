"""权限目录自愈：存量库缺节点 = 路由要求一个谁都没有的权限 = 整页 403。

背景（2026-10-11 实测症状）：`BUILTIN_ROLES` 后来补了 `study:record:read` 等节点，
但存量库建得更早 —— 节点与授权都不在，于是 `/stats/kpi`、`/stats/daily`、`/stats/overview`
对**所有人**返回 403，前端 `useSWR` 拿到 403 后 `data` 是 `undefined`，页面渲染成 0/null。
"""

from __future__ import annotations

from sqlalchemy import select

from chaoxing.core.services.users import (
    BUILTIN_ROLES,
    EXTRA_PERMISSIONS,
    PERMISSION_CATALOG,
    UserService,
)
from chaoxing.storage.models import Permission, Role, role_permissions
from chaoxing.storage.uow import UnitOfWork


def test_catalog_covers_templates_and_extra_nodes() -> None:
    """目录 = 角色模板 ∪ 额外节点；漏一个节点就会让对应路由永久 403。"""
    from_templates = {code for codes in BUILTIN_ROLES.values() for code in codes}

    assert from_templates <= set(PERMISSION_CATALOG)
    assert set(EXTRA_PERMISSIONS) <= set(PERMISSION_CATALOG)
    assert len(PERMISSION_CATALOG) == len(set(PERMISSION_CATALOG))


def test_admin_role_is_the_full_catalog() -> None:
    """`admin` 必须是全量（单管理员部署是常态）。

    2026-10-11 实测过反例：admin 模板只给平台管理面时，管理员自己的 `/groups` 是 403，
    账号页的分组功能直接不可用 —— 这种"管理员反而点不动"的割裂不能再来一次。
    """
    admin_nodes = set(BUILTIN_ROLES["admin"])

    assert admin_nodes == set(PERMISSION_CATALOG)
    for name, codes in BUILTIN_ROLES.items():
        assert set(codes) <= admin_nodes, f"{name} 有 admin 没有的节点：{set(codes) - admin_nodes}"


async def test_sync_repairs_stale_catalog(uow: UnitOfWork) -> None:
    """复刻症状：节点在旧库里根本不存在 → sync 补回节点与 admin 授权。"""
    service = UserService(uow=uow)
    await service.sync_catalog()

    # 手动制造"旧库"：节点连授权一起删掉
    async with uow.begin() as db:
        node_id = (
            await db.scalars(select(Permission.id).where(Permission.code == "study:record:read"))
        ).one()
        await db.execute(
            role_permissions.delete().where(role_permissions.c.permission_id == node_id)
        )
        await db.execute(Permission.__table__.delete().where(Permission.id == node_id))

    created = await service.sync_catalog()

    assert created["permissions"] >= 1
    async with uow.read() as db:
        granted = {
            str(code)
            for code in (
                await db.scalars(
                    select(Permission.code)
                    .select_from(role_permissions)
                    .join(Permission, Permission.id == role_permissions.c.permission_id)
                    .join(Role, Role.id == role_permissions.c.role_id)
                    .where(Role.name == "admin")
                )
            ).all()
        }
    assert "study:record:read" in granted
    assert created["grants"] >= 1


async def test_sync_is_idempotent(uow: UnitOfWork) -> None:
    """第二次跑不再新增（否则每次启动都写库）。"""
    service = UserService(uow=uow)
    await service.sync_catalog()

    assert await service.sync_catalog() == {"permissions": 0, "roles": 0, "grants": 0}
