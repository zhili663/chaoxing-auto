"""元数据端点：枚举与权限树的唯一来源。

来源：计划 §5.10 第三条红线 —— 前端**禁止硬编码**权限节点与枚举文案，全部由 `/api/meta/*` 下发。
这组用例同时钉住"两处不漂"：任务点类型与策略键直接取自代码常量，而不是这里手抄一份。
"""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from chaoxing.core.container import Container
from chaoxing.core.services.plans import DEFAULT_STRATEGY
from chaoxing.core.services.study import ALL_POINT_KINDS


def secret_of(password: str) -> str:
    """前端口径的口令。"""
    return hashlib.sha256(password.encode()).hexdigest()


def grant(client: TestClient, username: str, role: str) -> None:
    """直接经服务层授权（顺便把该角色的权限节点种进库）。"""
    import asyncio

    container: Container = client.app.state.container  # type: ignore[attr-defined]

    async def run() -> None:
        async with container.uow.read() as db:
            from sqlalchemy import select

            from chaoxing.storage.models import User

            user = (await db.scalars(select(User).where(User.username == username))).first()
        assert user is not None
        await container.users.grant(user.id, role)

    asyncio.run(run())


def register(client: TestClient, username: str, password: str = "passw0rd") -> None:
    """走完整注册流程（非首个用户需要 nonce）。"""
    nonce = client.get("/api/auth/challenge", params={"scene": "register"}).json()["nonce"]
    response = client.post(
        "/api/auth/register",
        json={"username": username, "secret": secret_of(password), "nonce": nonce},
    )
    assert response.status_code == 201, response.text


def test_enums_come_from_code_constants(logged_in: TestClient) -> None:
    """枚举与代码常量一致（不允许第二份手抄）。"""
    response = logged_in.get("/api/meta/enums")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["point_kinds"] == list(ALL_POINT_KINDS)
    assert set(body["strategy_keys"]) == set(DEFAULT_STRATEGY)
    assert "judge" in body["question_types"]
    assert body["task_statuses"]


def test_point_kinds_matches_study_constants(logged_in: TestClient) -> None:
    """单独的类型清单与枚举接口一致。"""
    assert logged_in.get("/api/meta/point-kinds").json() == list(ALL_POINT_KINDS)


def test_permissions_tree_covers_builtin_nodes(logged_in: TestClient) -> None:
    """权限树覆盖内置角色用到的节点（角色落库时顺便种下节点）。

    `admin` 与 `operator` 各管一摊（前者是用户/角色/设置/系统，后者是课程与刷课），
    所以这里把两个角色都给同一个人，再断言并集。
    """
    register(logged_in, "helper")
    grant(logged_in, "helper", "operator")
    grant(logged_in, "helper", "admin")

    rows = logged_in.get("/api/meta/permissions").json()

    codes = {row["code"] for row in rows}
    assert {"study:plan:write", "quiz:submit", "prompt:write", "system:doctor"} <= codes
    assert all(row["group"] for row in rows)


def test_meta_requires_login(client: TestClient) -> None:
    """没登录就是 401（元数据不是公开信息）。"""
    assert client.get("/api/meta/enums").status_code == 401
