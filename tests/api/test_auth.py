"""认证全链路：注册（首个用户即超管）→ 登录 → 我是谁 → 登出。

来源：计划 §5.8 的用户体系 —— 首个用户自动超管（否则新部署没人能授权）、
会话是 HttpOnly Cookie（JS 拿不到，XSS 也偷不走）。
"""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient


def client_secret(password: str) -> str:
    """前端口径的口令：``sha256(password)``。

    测试必须用真哈希值 —— 否则会悄悄建立在"服务端接受任意字符串"的假前提上
    （而那条路径恰恰是要被拒绝的）。
    """
    return hashlib.sha256(password.encode()).hexdigest()


def register(client: TestClient, username: str, password: str):
    """走完整注册流程（非首个用户必须携带一次性 nonce）。"""
    nonce = client.get("/api/auth/challenge", params={"scene": "register"}).json()["nonce"]
    return client.post(
        "/api/auth/register",
        json={"username": username, "secret": client_secret(password), "nonce": nonce},
    )


def test_first_user_becomes_superuser(client: TestClient) -> None:
    """首个注册用户必须自动成为超管。"""
    response = client.post(
        "/api/auth/register",
        json={"username": "admin", "secret": client_secret("passw0rd")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["is_superuser"] is True
    assert body["permissions"] == ["*"]


def test_second_user_is_not_superuser(client: TestClient) -> None:
    """第二个用户不是超管（权限要靠授权得到）。"""
    register(client, "admin", "passw0rd")

    response = register(client, "bob", "bob-pass")

    assert response.status_code == 201, response.text
    assert response.json()["is_superuser"] is False


def test_second_user_without_nonce_is_rejected(client: TestClient) -> None:
    """非首个用户的注册必须带一次性 nonce（防重放）。"""
    register(client, "admin", "passw0rd")

    response = client.post(
        "/api/auth/register",
        json={"username": "bob", "secret": client_secret("bob-pass")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_nonce"


def test_nonce_cannot_be_reused(client: TestClient) -> None:
    """同一个 nonce 用两次必须失败（一次性是它的全部意义）。"""
    register(client, "admin", "passw0rd")
    nonce = client.get("/api/auth/challenge", params={"scene": "register"}).json()["nonce"]
    payload = {"username": "bob", "secret": client_secret("bob-pass"), "nonce": nonce}

    first = client.post("/api/auth/register", json=payload)
    second = client.post(
        "/api/auth/register",
        json={**payload, "username": "carol"},
    )

    assert first.status_code == 201
    assert second.status_code == 400


def test_register_rejects_plaintext_looking_secret(client: TestClient) -> None:
    """传明文（或长度不对的东西）必须被拦下 —— 口径不一致会让用户永远登不上。"""
    response = client.post(
        "/api/auth/register",
        json={"username": "admin", "secret": "passw0rd"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_duplicate_username_conflicts(client: TestClient) -> None:
    """重名注册返回 409（而不是静默覆盖别人的账号）。"""
    register(client, "admin", "passw0rd")

    response = register(client, "admin", "other")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "user_exists"


def test_login_sets_httponly_cookie(client: TestClient, registered: dict[str, str]) -> None:
    """登录成功要下发 HttpOnly Cookie（JS 拿不到 token）。"""
    response = client.post("/api/auth/login", json=registered)

    assert response.status_code == 200
    cookie_header = response.headers.get("set-cookie", "")
    assert "cx_session=" in cookie_header
    assert "HttpOnly" in cookie_header
    assert "SameSite=lax" in cookie_header.lower() or "samesite=lax" in cookie_header.lower()


def test_login_with_wrong_password_is_unauthorized(client: TestClient) -> None:
    """口令错误返回 401，且**不区分**用户是否存在（不给撞库提供信号）。"""
    client.post(
        "/api/auth/register",
        json={"username": "admin", "secret": client_secret("passw0rd")},
    )

    wrong = client.post(
        "/api/auth/login", json={"username": "admin", "secret": client_secret("nope")}
    )
    ghost = client.post(
        "/api/auth/login", json={"username": "ghost", "secret": client_secret("nope")}
    )

    assert wrong.status_code == 401
    assert ghost.status_code == 401
    assert wrong.json()["error"]["message"] == ghost.json()["error"]["message"]


def test_me_requires_login(client: TestClient) -> None:
    """未登录访问 /auth/me 返回 401（前端据此跳登录）。"""
    response = client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_me_returns_user_after_login(client: TestClient, registered: dict[str, str]) -> None:
    """登录后 /auth/me 返回用户与权限。"""
    client.post("/api/auth/login", json=registered)

    response = client.get("/api/auth/me")

    assert response.status_code == 200
    assert response.json()["username"] == "admin"


def test_logout_revokes_session(client: TestClient, registered: dict[str, str]) -> None:
    """登出后凭原来的 Cookie 不能再访问（会话被撤销，不是前端假装忘记）。"""
    client.post("/api/auth/login", json=registered)
    token = client.cookies.get("cx_session")

    assert client.post("/api/auth/logout").status_code == 200
    # 手工把旧 token 塞回去，模拟"被偷走的 Cookie"
    client.cookies.set("cx_session", token or "")
    response = client.get("/api/auth/me")

    assert response.status_code == 401


def test_health_is_public(client: TestClient) -> None:
    """健康检查不需要登录（编排与探针要用）。"""
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_system_overview_requires_permission(client: TestClient) -> None:
    """运行概览受权限节点保护（未登录拿不到）。"""
    response = client.get("/api/system/overview")

    assert response.status_code == 401


def test_superuser_can_read_system_overview(client: TestClient, logged_in: TestClient) -> None:
    """超管能看运行概览。"""
    response = logged_in.get("/api/system/overview")

    assert response.status_code == 200
    body = response.json()
    assert "kpi" in body
    assert body["bus"]["subscribers"] >= 3  # 三个事件订阅者
