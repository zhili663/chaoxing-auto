"""账户组端点：批量入口，但**不许**成为越权通道。

两组独立结论分开钉：① 成员增减本身可用（含路径式的单个移除）；② 塞进不可见的账号
必须 404 —— 组的可见性不放松账号的 `owner_user_id` 规则（跨用户场景见
`tests/api/test_accounts.py`，这里只钉"组不会把不可见账号变可见"）。
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient


def secret_of(password: str) -> str:
    """前端口径的口令。"""
    return hashlib.sha256(password.encode()).hexdigest()


def register(client: TestClient, username: str, password: str = "passw0rd") -> None:
    """走完整注册流程并登录（首个用户即超管）。"""
    nonce = client.get("/api/auth/challenge", params={"scene": "register"}).json()["nonce"]
    created = client.post(
        "/api/auth/register",
        json={"username": username, "secret": secret_of(password), "nonce": nonce},
    )
    assert created.status_code == 201, created.text
    logged_in = client.post(
        "/api/auth/login", json={"username": username, "secret": secret_of(password)}
    )
    assert logged_in.status_code == 200, logged_in.text


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    """超管（首个用户持通配权限）。"""
    register(client, "admin")
    return client


def make_account(client: TestClient, username: str = "13800000000") -> int:
    """建一个超星账号实体（不触发登录）。"""
    response = client.post("/api/accounts", json={"username": username})

    assert response.status_code in (200, 201), response.text
    return int(response.json()["id"])


def test_member_add_then_remove_by_path(admin: TestClient) -> None:
    """加成员 → 成员数变 1；路径式 DELETE 移出 → 变 0（客户端不必带 body）。"""
    account_id = make_account(admin)
    group_id = admin.post("/api/groups", json={"name": "验收组"}).json()["id"]

    added = admin.post(f"/api/groups/{group_id}/members", json={"account_ids": [account_id]})
    assert added.status_code == 200, added.text
    assert added.json() == {"changed": 1, "members": 1}

    removed = admin.delete(f"/api/groups/{group_id}/members/{account_id}")
    assert removed.status_code == 200, removed.text
    assert removed.json() == {"changed": 1, "members": 0}


def test_unknown_account_is_404(admin: TestClient) -> None:
    """塞进一个不可见的账号 → 404（组不会把不可见账号变可见）。"""
    group_id = admin.post("/api/groups", json={"name": "验收组2"}).json()["id"]

    response = admin.post(f"/api/groups/{group_id}/members", json={"account_ids": [9999]})

    assert response.status_code == 404
