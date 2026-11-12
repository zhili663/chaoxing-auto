"""刷课计划域：CRUD 闭环 + 权限 + 数据可见性。

来源：

* 计划 §5.11 的「刷什么」链路 —— 计划（账号 + 课程集合 + 策略）是**唯一权威来源**；
* §5.8 的数据范围要求 —— 越权一律 **404 而不是 403**（403 等于确认资源存在）。

计划此前只有服务层没有入口（`PlanService` 装配在容器里但没人调用），
这组用例把「有服务、没入口」这个半成品状态钉住。
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from fastapi.testclient import TestClient

from chaoxing.core.container import Container
from chaoxing.storage.models import Account, Course


def secret_of(password: str) -> str:
    """前端口径的口令：``sha256(password)``。"""
    return hashlib.sha256(password.encode()).hexdigest()


def register(client: TestClient, username: str, password: str = "passw0rd") -> None:
    """走完整注册流程（非首个用户需要 nonce）。"""
    nonce = client.get("/api/auth/challenge", params={"scene": "register"}).json()["nonce"]
    response = client.post(
        "/api/auth/register",
        json={"username": username, "secret": secret_of(password), "nonce": nonce},
    )
    assert response.status_code == 201, response.text


def seed_account_and_courses(client: TestClient, *, owner_user_id: int | None = 1) -> None:
    """直接经容器造数据（账号 1 + 两门课，其中一门已勾选）。

    :param owner_user_id: 账号属主。``None`` = **共享账号**（可空属主是既定语义，
        见 ``accounts/schemas.py`` 与 ``groups.py``）—— 共享账号对所有人可见，
        所以"看不到别人的数据"这条要用**有属主**的账号来测。
    """
    container: Container = client.app.state.container  # type: ignore[attr-defined]

    async def prepare() -> None:
        async with container.uow.begin() as db:
            account = Account(username="13800000000", owner_user_id=owner_user_id)
            db.add(account)
            await db.flush()
            db.add(
                Course(
                    account_id=account.id,
                    course_key="265361351:151148348",
                    title="课程 A",
                    selected=True,
                )
            )
            db.add(Course(account_id=account.id, course_key="265266890:154823229", title="课程 B"))

    asyncio.run(prepare())


def grant(client: TestClient, username: str, role: str) -> None:
    """直接经服务层授权。

    **新注册用户默认没有任何角色**（首个用户例外：自动超管）——
    所以"普通用户"的用例必须显式授予 ``viewer``，否则拿到的是 403 而不是数据范围判定。
    """
    container: Container = client.app.state.container  # type: ignore[attr-defined]

    async def run() -> None:
        async with container.uow.read() as db:
            from sqlalchemy import select

            from chaoxing.storage.models import User

            user = (await db.scalars(select(User).where(User.username == username))).first()
        assert user is not None
        await container.users.grant(user.id, role)

    asyncio.run(run())


@pytest.fixture
def seeded(logged_in: TestClient) -> TestClient:
    """超管 + 一个账号 + 两门课。"""
    seed_account_and_courses(logged_in)
    return logged_in


def test_plan_crud_roundtrip(seeded: TestClient) -> None:
    """建 → 列 → 读 → 停用 → 删；`course_key` 必须被解析成 `course_id`。"""
    created = seeded.post(
        "/api/study/plans",
        json={
            "name": "今晚刷 A",
            "account_id": 1,
            "course_keys": ["265361351:151148348"],
            "strategy": {"quiz": False},
        },
    )
    assert created.status_code == 201, created.text
    plan = created.json()
    assert plan["name"] == "今晚刷 A"
    assert plan["course_ids"], "course_key 必须被解析成 course_id"
    assert plan["strategy"]["quiz"] is False
    assert plan["strategy"]["video"] is True, "策略要与默认值合并"

    listed = seeded.get("/api/study/plans", params={"account_id": 1})
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()] == [plan["id"]]

    patched = seeded.patch(f"/api/study/plans/{plan['id']}", json={"enabled": False})
    assert patched.status_code == 200, patched.text
    assert seeded.get(f"/api/study/plans/{plan['id']}").json()["enabled"] is False

    assert seeded.delete(f"/api/study/plans/{plan['id']}").status_code == 204
    assert seeded.get(f"/api/study/plans/{plan['id']}").status_code == 404


def test_plan_write_requires_permission(client: TestClient, registered: dict[str, str]) -> None:
    """viewer 能读（`study:plan:read`）、不能写（403）。"""
    seed_account_and_courses(client)
    register(client, "looker")
    grant(client, "looker", "viewer")
    login = client.post(
        "/api/auth/login", json={"username": "looker", "secret": secret_of("passw0rd")}
    )
    assert login.status_code == 200, login.text

    payload = {"name": "越权计划", "account_id": 1, "course_keys": []}
    assert client.post("/api/study/plans", json=payload).status_code == 403
    # 读路由有权限，但**看不到别人的账号** —— 与不存在同样处理（404）
    assert client.get("/api/study/plans", params={"account_id": 1}).status_code == 404


def test_plan_list_requires_account_for_non_admin(client: TestClient, registered) -> None:
    """普通用户不指定账号看列表要被拒（否则"不传参数"等于把全库送给他）。"""
    seed_account_and_courses(client)
    register(client, "looker")
    grant(client, "looker", "viewer")
    client.post("/api/auth/login", json={"username": "looker", "secret": secret_of("passw0rd")})

    response = client.get("/api/study/plans")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "account_required"


def test_plan_cross_account_is_404(seeded: TestClient) -> None:
    """超管建的计划，换个用户按 id 直接取 → 404（不是 403）。"""
    seed_account_and_courses(seeded)
    plan_id = seeded.post(
        "/api/study/plans", json={"name": "超管的计划", "account_id": 1, "course_keys": []}
    ).json()["id"]

    register(seeded, "looker")
    grant(seeded, "looker", "viewer")
    seeded.post("/api/auth/login", json={"username": "looker", "secret": secret_of("passw0rd")})

    assert seeded.get(f"/api/study/plans/{plan_id}").status_code == 404


def test_shared_account_is_visible_to_everyone(client: TestClient, registered) -> None:
    """**无属主 = 共享账号**（既定语义）：普通用户也能看到并给它建计划。"""
    seed_account_and_courses(client, owner_user_id=None)
    register(client, "looker")
    grant(client, "looker", "viewer")
    client.post("/api/auth/login", json={"username": "looker", "secret": secret_of("passw0rd")})

    assert client.get("/api/study/plans", params={"account_id": 1}).status_code == 200
