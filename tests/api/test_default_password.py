"""默认口令（手机号后 6 位）的接口层。

口径（2026-10-13 用户要求）：管理员建普通用户时**可以不填初始口令**，
默认口令 = 注册手机号的**后 6 位**；管理员角色必须显式设置；
既没口令也没手机号 → 明确报错（不能凭空造口令）。

契约是加法式的：`POST /users` 的 `secret` 由必填变可选，其余字段与响应一字未动。
"""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient


def client_secret(password: str) -> str:
    """前端口径的口令：``sha256(password)``。"""
    return hashlib.sha256(password.encode()).hexdigest()


def register_first_user(client: TestClient) -> None:
    """注册首个用户（自动超管）并**登录** —— 建号接口需要已登录会话。"""
    response = client.post(
        "/api/auth/register",
        json={"username": "admin", "secret": client_secret("adm1npass")},
    )
    assert response.status_code == 201, response.text
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "secret": client_secret("adm1npass")},
    )
    assert login.status_code == 200, login.text


def test_create_without_secret_logs_in_with_phone_tail(client: TestClient) -> None:
    """不传初始口令建号 → 用「手机号后 6 位」就能登录（端到端）。"""
    register_first_user(client)

    created = client.post(
        "/api/users",
        json={"username": "stu1", "phone": "13800001234", "role": "viewer"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["username"] == "stu1"

    login = client.post(
        "/api/auth/login",
        json={"username": "13800001234", "secret": client_secret("001234")},
    )
    assert login.status_code == 200, login.text
    assert login.json()["username"] == "stu1"


def test_admin_role_requires_explicit_secret(client: TestClient) -> None:
    """管理员角色不套用默认口令：留空必须被拒（否则等于把管理员设成可猜口令）。"""
    register_first_user(client)

    response = client.post(
        "/api/users",
        json={"username": "boss", "phone": "13900005678", "role": "admin"},
    )

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "invalid_secret"


def test_without_secret_and_without_phone_is_rejected(client: TestClient) -> None:
    """没有手机号就没有默认口令可用 —— 报错，而不是建一个空口令账号。"""
    register_first_user(client)

    response = client.post("/api/users", json={"username": "nobody", "role": "viewer"})

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "invalid_secret"


def test_explicit_secret_still_wins(client: TestClient) -> None:
    """显式传口令时一切照旧（回归：默认逻辑不能盖掉调用方的选择）。"""
    register_first_user(client)

    created = client.post(
        "/api/users",
        json={
            "username": "stu2",
            "phone": "13700004321",
            "secret": client_secret("own-pass-123"),
            "role": "viewer",
        },
    )
    assert created.status_code == 200, created.text

    ok = client.post(
        "/api/auth/login",
        json={"username": "13700004321", "secret": client_secret("own-pass-123")},
    )
    assert ok.status_code == 200, ok.text

    bad = client.post(
        "/api/auth/login",
        json={"username": "13700004321", "secret": client_secret("004321")},
    )
    assert bad.status_code == 401, bad.text
