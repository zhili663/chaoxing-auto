"""手机号登录的接口层：注册回显、撞号 409、改号后可登录。

契约是**加法式**的：请求体里仍叫 ``username``（值可以是手机号），只是多了可选的
``phone``；响应里追加 ``phone``。这里用 ``username=<手机号>`` 登录，正是为了钉住
"字段名不改、语义放宽"这件事 —— 前端零改动就能用。
"""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient


def client_secret(password: str) -> str:
    """前端口径的口令：``sha256(password)``。"""
    return hashlib.sha256(password.encode()).hexdigest()


def register(client: TestClient, username: str, password: str, phone: str | None = None):
    """走完整注册流程（非首个用户必须携带一次性 nonce）。"""
    nonce = client.get("/api/auth/challenge", params={"scene": "register"}).json()["nonce"]
    payload: dict[str, str] = {
        "username": username,
        "secret": client_secret(password),
        "nonce": nonce,
    }
    if phone is not None:
        payload["phone"] = phone
    return client.post("/api/auth/register", json=payload)


def test_register_with_phone_echoes_it(client: TestClient) -> None:
    """注册时带手机号，响应里的 ``UserOut.phone`` 要原样回显。"""
    nonce = client.get("/api/auth/challenge", params={"scene": "register"}).json()["nonce"]
    response = client.post(
        "/api/auth/register",
        json={
            "username": "admin",
            "secret": client_secret("passw0rd"),
            "phone": "13900000000",
            "nonce": nonce,
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["phone"] == "13900000000"


def test_register_without_phone_is_null(client: TestClient) -> None:
    """不填手机号时回显 ``None``（是 NULL，不是空串）。"""
    response = client.post(
        "/api/auth/register",
        json={"username": "admin", "secret": client_secret("passw0rd")},
    )

    assert response.status_code == 201, response.text
    assert response.json()["phone"] is None


def test_duplicate_phone_conflicts(client: TestClient) -> None:
    """手机号已被占用时注册返回 409 ``user_exists``（不能悄悄建出同号账号）。"""
    assert register(client, "admin", "passw0rd", phone="13900000000").status_code == 201

    response = register(client, "bob", "bob-pass", phone="13900000000")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "user_exists"


def test_login_with_phone_reaches_me(client: TestClient) -> None:
    """用手机号当 ``username`` 登录 → ``/auth/me`` 返回该用户。"""
    assert register(client, "admin", "passw0rd", phone="13900000000").status_code == 201

    login = client.post(
        "/api/auth/login",
        json={"username": "13900000000", "secret": client_secret("passw0rd")},
    )
    assert login.status_code == 200, login.text
    assert login.json()["username"] == "admin"

    me = client.get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["username"] == "admin"
    assert me.json()["phone"] == "13900000000"


def test_patch_phone_then_login_with_new_number(logged_in: TestClient) -> None:
    """``PATCH /users/{id}`` 改号后，新手机号即可登录（无需知道用户名）。"""
    created = logged_in.post(
        "/api/users",
        json={"username": "carol", "secret": client_secret("carol-pass"), "role": "viewer"},
    )
    assert created.status_code == 200, created.text
    user_id = created.json()["id"]

    patched = logged_in.patch(f"/api/users/{user_id}", json={"phone": "13800000001"})
    assert patched.status_code == 200, patched.text

    login = logged_in.post(
        "/api/auth/login",
        json={"username": "13800000001", "secret": client_secret("carol-pass")},
    )
    assert login.status_code == 200, login.text
    assert login.json()["username"] == "carol"


def test_patch_phone_to_taken_number_conflicts(logged_in: TestClient) -> None:
    """改号撞到别人的号码时返回 409 ``user_exists``。"""
    first = logged_in.post(
        "/api/users",
        json={
            "username": "carol",
            "secret": client_secret("carol-pass"),
            "phone": "13900000002",
            "role": "viewer",
        },
    )
    assert first.status_code == 200, first.text
    second = logged_in.post(
        "/api/users",
        json={"username": "dave", "secret": client_secret("dave-pass"), "role": "viewer"},
    )
    assert second.status_code == 200, second.text

    response = logged_in.patch(f"/api/users/{second.json()['id']}", json={"phone": "13900000002"})

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "user_exists"
