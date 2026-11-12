"""审计中间件：写操作留痕、读操作不记、失败也要记。

来源：四通道分工里的"行为审计"—— 出了问题要能回答"谁在什么时候对哪个资源做了什么、
结果如何、从哪来"。三条判断写进了实现，也写进了这些用例：
GET 不记（量大且无副作用）、失败要记（那正是最需要回溯的）、**绝不记录请求体**
（里面可能有明文口令）。
"""

from __future__ import annotations

import asyncio
import hashlib

from fastapi.testclient import TestClient
from sqlalchemy import select

from chaoxing.core.container import Container
from chaoxing.storage.models import AuditLog


def secret_of(password: str) -> str:
    """前端口径的口令。"""
    return hashlib.sha256(password.encode()).hexdigest()


def register(client: TestClient, username: str = "admin", password: str = "passw0rd") -> None:
    """注册并登录一个超管。"""
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


def audit_rows(client: TestClient) -> list[AuditLog]:
    """读全部审计行（测试里直接查库）。"""
    container: Container = client.app.state.container  # type: ignore[attr-defined]

    async def _load() -> list[AuditLog]:
        async with container.uow.read() as db:
            return list((await db.scalars(select(AuditLog).order_by(AuditLog.id))).all())

    return asyncio.run(_load())


def test_write_operation_is_audited(client: TestClient) -> None:
    """写操作要留下一条审计（含操作者、动作、结果、request_id）。"""
    register(client)
    before = len(audit_rows(client))

    client.post("/api/accounts", json={"username": "13800000000"})

    rows = audit_rows(client)
    assert len(rows) == before + 1
    latest = rows[-1]
    assert latest.action == "accounts.create"
    assert latest.result == "ok"
    assert latest.user_id is not None
    assert latest.request_id


def test_read_operation_is_not_audited(client: TestClient) -> None:
    """GET 不记（量大且无副作用，记了只会淹没真正重要的行）。"""
    register(client)
    client.post("/api/accounts", json={"username": "13800000001"})
    before = len(audit_rows(client))

    client.get("/api/accounts")
    client.get("/api/stats/kpi")

    assert len(audit_rows(client)) == before


def test_failed_write_is_recorded_as_denied(client: TestClient) -> None:
    """被拒绝的写操作也要记 —— 403 正是最需要回溯的信号。"""
    nonce = client.get("/api/auth/challenge", params={"scene": "register"}).json()["nonce"]
    client.post(
        "/api/auth/register",
        json={"username": "admin", "secret": secret_of("passw0rd"), "nonce": nonce},
    )

    # 未登录时调写接口 → 401
    client.post("/api/accounts", json={"username": "13800000002"})

    rows = audit_rows(client)
    assert any(row.result == "denied" for row in rows), "失败请求没留痕"


def test_audit_never_stores_request_body(client: TestClient) -> None:
    """审计里**不能**出现请求体内容（可能含明文口令）。"""
    register(client)

    client.post("/api/accounts", json={"username": "13800000003", "password": "plain-secret-value"})

    latest = audit_rows(client)[-1]
    assert latest.detail == {"method": "POST", "status": 201}
    assert "plain-secret-value" not in str(latest.detail)


def test_response_carries_request_id(client: TestClient) -> None:
    """每个响应都带 ``x-request-id``（串联日志、事件与审计）。"""
    response = client.get("/api/health")

    assert response.headers.get("x-request-id")


def test_client_supplied_request_id_is_kept(client: TestClient) -> None:
    """调用方给了 ``x-request-id`` 就沿用（跨服务追踪）。"""
    response = client.get("/api/health", headers={"x-request-id": "abc123"})

    assert response.headers["x-request-id"] == "abc123"
