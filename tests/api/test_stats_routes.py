"""统计域：KPI 总览 / 热力图 / 按属主聚合。

来源：计划 §5.11 的 stats 路由组，以及 §5.8 的数据范围要求 ——
`/stats/users` 是跨账号视图，只有持 `account:read:all` 的人能看；
普通用户不指定账号一律 400（"不传参数"不能等于全库合计）。
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from fastapi.testclient import TestClient

from chaoxing.core.container import Container
from chaoxing.storage.models import Account, Course, TaskPoint


def secret_of(password: str) -> str:
    """前端口径的口令。"""
    return hashlib.sha256(password.encode()).hexdigest()


def register(client: TestClient, username: str, password: str = "passw0rd") -> None:
    """走完整注册流程（非首个用户需要 nonce）。"""
    nonce = client.get("/api/auth/challenge", params={"scene": "register"}).json()["nonce"]
    response = client.post(
        "/api/auth/register",
        json={"username": username, "secret": secret_of(password), "nonce": nonce},
    )
    assert response.status_code == 201, response.text


def grant(client: TestClient, username: str, role: str) -> None:
    """直接经服务层授权（新注册用户默认没有角色）。"""
    container: Container = client.app.state.container  # type: ignore[attr-defined]

    async def run() -> None:
        async with container.uow.read() as db:
            from sqlalchemy import select

            from chaoxing.storage.models import User

            user = (await db.scalars(select(User).where(User.username == username))).first()
        assert user is not None
        await container.users.grant(user.id, role)

    asyncio.run(run())


def seed(client: TestClient, *, owner_user_id: int | None = 1) -> None:
    """造一个账号 + 一门课 + 一个已完成任务点。"""
    container: Container = client.app.state.container  # type: ignore[attr-defined]

    async def prepare() -> None:
        async with container.uow.begin() as db:
            account = Account(username="13800000000", owner_user_id=owner_user_id)
            db.add(account)
            await db.flush()
            course = Course(account_id=account.id, course_key="1:2", title="课", selected=True)
            db.add(course)
            await db.flush()
            db.add(
                TaskPoint(
                    account_id=account.id,
                    course_id=course.id,
                    point_id="p1",
                    kind="video",
                    status="done",
                )
            )

    asyncio.run(prepare())


@pytest.fixture
def admin(logged_in: TestClient) -> TestClient:
    """超管 + 一份数据。"""
    seed(logged_in)
    return logged_in


def test_overview_shape(admin: TestClient) -> None:
    """KPI 总览三块：kpi / today / accounts。"""
    response = admin.get("/api/stats/overview", params={"account_id": 1})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kpi"]["points_done"] == 1
    assert body["accounts"] == 1
    assert body["today"] is not None
    assert "day" in body["today"]


def test_heatmap_length_matches_weeks(admin: TestClient) -> None:
    """热力图返回 `weeks × 7` 天（缺的日期由服务层补 0）。"""
    rows = admin.get("/api/stats/heatmap", params={"account_id": 1, "weeks": 2}).json()

    assert len(rows) == 14
    assert all(len(row["day"]) == 10 for row in rows)


def test_users_aggregates_by_owner(admin: TestClient) -> None:
    """按属主聚合：属主 1 一行、共享账号（属主 0/None）一行。"""
    seed(admin, owner_user_id=None)

    rows = admin.get("/api/stats/users").json()

    by_owner = {row["owner_user_id"]: row for row in rows}
    assert by_owner[1]["points_done"] == 1
    assert by_owner[None]["accounts"] == 1


def test_users_requires_global_read(client: TestClient, registered) -> None:
    """`/stats/users` 是跨账号视图：operator 拿不到 `account:read:all`，403。"""
    register(client, "operator")
    grant(client, "operator", "operator")
    client.post("/api/auth/login", json={"username": "operator", "secret": secret_of("passw0rd")})

    assert client.get("/api/stats/users").status_code == 403


def test_stats_requires_account_for_non_admin(client: TestClient, registered) -> None:
    """普通用户不指定账号 → 400（不能把全库合计送出去）。"""
    seed(client)
    register(client, "operator")
    grant(client, "operator", "operator")
    client.post("/api/auth/login", json={"username": "operator", "secret": secret_of("passw0rd")})

    response = client.get("/api/stats/overview")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "account_required"


def test_foreign_account_heatmap_is_404(client: TestClient, registered) -> None:
    """别人的账号按 id 取统计 → 404（不是 403）。"""
    seed(client)
    register(client, "operator")
    grant(client, "operator", "operator")
    client.post("/api/auth/login", json={"username": "operator", "secret": secret_of("passw0rd")})

    assert client.get("/api/stats/heatmap", params={"account_id": 1}).status_code == 404


def test_admin_role_reaches_kpi(client: TestClient, registered) -> None:
    """回归（2026-10-11 线上症状）：admin 角色必须能拿到 KPI，而不是整页 403。

    当时的故障是"路由要求 `study:record:read`，而库里没有这个节点" —— 连管理员都被拒，
    前端于是渲染成 0/null。这条用例跑**真装配路径**（lifespan 里 `sync_catalog()` 补齐节点与授权），
    所以它红了就说明自愈失效、KPI 又会全 0。
    """
    seed(client)
    register(client, "platform-admin")
    grant(client, "platform-admin", "admin")
    client.post(
        "/api/auth/login", json={"username": "platform-admin", "secret": secret_of("passw0rd")}
    )

    response = client.get("/api/stats/kpi")

    assert response.status_code == 200, response.text
    assert response.json()["points_total"] == 1
