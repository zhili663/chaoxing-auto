"""用户与角色管理：只有管理员能建号与授权。

来源：注册接口只解决"第一个超管"，其余账号的建立与授权必须有明确入口 ——
而且这个入口本身要受权限保护（否则任何登录用户都能给自己发 admin）。
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from chaoxing.core.container import Container


def secret_of(password: str) -> str:
    """前端口径的口令。"""
    return hashlib.sha256(password.encode()).hexdigest()


def register(client: TestClient, username: str, password: str = "passw0rd") -> None:
    """走完整注册流程并登录。"""
    nonce = client.get("/api/auth/challenge", params={"scene": "register"}).json()["nonce"]
    response = client.post(
        "/api/auth/register",
        json={"username": username, "secret": secret_of(password), "nonce": nonce},
    )
    assert response.status_code == 201, response.text
    assert (
        client.post(
            "/api/auth/login", json={"username": username, "secret": secret_of(password)}
        ).status_code
        == 200
    )


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    """已登录的超管。"""
    register(client, "admin")
    return client


@pytest.fixture
def viewer(admin: TestClient) -> Iterator[TestClient]:
    """只有 viewer 角色的普通用户（独立 Cookie，确保不是超管）。"""
    from starlette.testclient import TestClient as StarletteClient

    with StarletteClient(admin.app) as peer:
        created = admin.post(
            "/api/users",
            json={
                "username": "carol",
                "secret": secret_of("carol-pass"),
                "role": "viewer",
            },
        )
        assert created.status_code == 200, created.text
        assert (
            peer.post(
                "/api/auth/login", json={"username": "carol", "secret": secret_of("carol-pass")}
            ).status_code
            == 200
        )
        yield peer


def test_admin_lists_users(admin: TestClient) -> None:
    """管理员能列出用户，并能看到角色。"""
    response = admin.get("/api/users")

    assert response.status_code == 200
    usernames = [item["username"] for item in response.json()]
    assert "admin" in usernames


def test_create_user_with_role(admin: TestClient) -> None:
    """管理员建号时可一并指定初始角色。"""
    response = admin.post(
        "/api/users",
        json={"username": "dave", "secret": secret_of("dave-pass"), "role": "operator"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_superuser"] is False
    assert body["roles"] == ["operator"]


def test_created_user_has_the_role_permissions(admin: TestClient, client: TestClient) -> None:
    """被授予 operator 的用户真的拿到了对应权限（不是只有个标签）。"""
    admin.post(
        "/api/users",
        json={"username": "erin", "secret": secret_of("erin-pass"), "role": "operator"},
    )

    from starlette.testclient import TestClient as StarletteClient

    with StarletteClient(admin.app) as peer:
        peer.post("/api/auth/login", json={"username": "erin", "secret": secret_of("erin-pass")})
        me = peer.get("/api/auth/me").json()

    assert "account:write" in me["permissions"]
    assert "user:write" not in me["permissions"]  # operator 不该有用户管理权


def test_viewer_cannot_list_users(viewer: TestClient) -> None:
    """普通用户不能列用户（否则等于给出别人的账号清单）。"""
    response = viewer.get("/api/users")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_viewer_cannot_grant_roles(viewer: TestClient) -> None:
    """普通用户不能给自己或别人授权 —— 这是最要紧的一条。"""
    response = viewer.post("/api/users/1/roles", json={"role": "admin"})

    assert response.status_code == 403


def test_roles_endpoint_lists_builtin_roles(admin: TestClient) -> None:
    """角色清单可供前端渲染授权下拉。"""
    response = admin.get("/api/roles")

    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {"viewer", "operator", "admin"}
    assert "account:read" in body["viewer"]


def test_grant_and_revoke_role(admin: TestClient) -> None:
    """授权与收权都要真的生效。"""
    created = admin.post(
        "/api/users", json={"username": "frank", "secret": secret_of("frank-pass")}
    ).json()
    user_id = created["id"]

    granted = admin.post(f"/api/users/{user_id}/roles", json={"role": "admin"})
    assert granted.status_code == 200
    assert "admin" in granted.json()["roles"]

    revoked = admin.delete(f"/api/users/{user_id}/roles/admin")
    assert revoked.status_code == 200
    assert "admin" not in revoked.json()["roles"]


def test_missing_user_is_404(admin: TestClient) -> None:
    """给不存在的用户授权返回 404。"""
    response = admin.post("/api/users/9999/roles", json={"role": "viewer"})

    assert response.status_code == 404


def test_container_has_user_service(client: TestClient) -> None:
    """装配完整性：容器里确实有用户服务（防止路由与容器脱节）。"""
    container: Container = client.app.state.container  # type: ignore[attr-defined]

    assert asyncio.run(container.users.list_users()) is not None


def test_cannot_deactivate_self(admin: TestClient) -> None:
    """不能停用自己 —— 否则一点确认就把自己锁在门外。"""
    me = admin.get("/api/auth/me").json()

    response = admin.patch(f"/api/users/{me['id']}", json={"is_active": False})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "cannot_modify_self"


def test_deactivate_blocks_login(admin: TestClient) -> None:
    """停用之后，口令正确也登不进来（停用要有实际效果，不只是列表上一个标记）。"""
    user_id = admin.post(
        "/api/users", json={"username": "gina", "secret": secret_of("gina-pass")}
    ).json()["id"]
    assert admin.patch(f"/api/users/{user_id}", json={"is_active": False}).status_code == 200

    from starlette.testclient import TestClient as StarletteClient

    with StarletteClient(admin.app) as peer:
        response = peer.post(
            "/api/auth/login", json={"username": "gina", "secret": secret_of("gina-pass")}
        )

    assert response.status_code == 401


def test_cannot_delete_self(admin: TestClient) -> None:
    """不能删自己。"""
    me = admin.get("/api/auth/me").json()

    response = admin.delete(f"/api/users/{me['id']}")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "cannot_modify_self"


def test_delete_user_removes_it(admin: TestClient) -> None:
    """删除后列表里就没有了（与停用区分开：一个可恢复、一个不可）。"""
    user_id = admin.post(
        "/api/users", json={"username": "hank", "secret": secret_of("hank-pass")}
    ).json()["id"]

    assert admin.delete(f"/api/users/{user_id}").status_code == 200

    remaining = [item["username"] for item in admin.get("/api/users").json()]
    assert "hank" not in remaining
    assert admin.get(f"/api/users/{user_id}/sessions").status_code == 404


def test_sessions_endpoint_hides_token(admin: TestClient) -> None:
    """会话列表说明"谁在哪登录过"，但**永远不回显 token**（库里只有指纹）。"""
    me = admin.get("/api/auth/me").json()

    rows = admin.get(f"/api/users/{me['id']}/sessions").json()

    assert rows, "登录之后至少有一条会话"
    assert {"id", "ip", "user_agent", "created_at", "revoked_at"} <= set(rows[0])
    assert "token" not in rows[0] and "token_hash" not in rows[0]


def test_viewer_cannot_modify_users(viewer: TestClient) -> None:
    """普通用户既不能停用别人，也不能删人。"""
    assert viewer.patch("/api/users/1", json={"is_active": False}).status_code == 403
    assert viewer.delete("/api/users/1").status_code == 403
