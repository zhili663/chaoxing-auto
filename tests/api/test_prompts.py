"""提示词端点：读得到、写得进、版本递增、权限分明。

来源：计划 §5.11 的 settings 路由表（`GET·PUT /settings/prompts`）。
`prompts` 表此前没有读取方，这组用例把它从"死代码"变成可达接口。
"""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from chaoxing.core.container import Container


def secret_of(password: str) -> str:
    """前端口径的口令。"""
    return hashlib.sha256(password.encode()).hexdigest()


def grant(client: TestClient, username: str, role: str) -> None:
    """直接经服务层授权（新注册用户默认没有任何角色）。"""
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


def test_put_then_get_prompt(logged_in: TestClient) -> None:
    """写入 → 列表可见 → 再写版本递增。"""
    first = logged_in.put(
        "/api/settings/prompts/quiz.system", json={"content": "只输出 JSON，不要解释。"}
    )
    assert first.status_code == 200, first.text
    assert first.json()["version"] == 1
    assert first.json()["content"] == "只输出 JSON，不要解释。"

    listed = logged_in.get("/api/settings/prompts")
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert [row["key"] for row in rows] == ["quiz.system"]
    assert rows[0]["version"] == 1

    second = logged_in.put("/api/settings/prompts/quiz.system", json={"content": "第二版"})
    assert second.status_code == 200
    assert second.json()["version"] == 2


def test_unknown_prompt_is_empty_list(logged_in: TestClient) -> None:
    """没写过任何提示词时返回空列表（不是 404）。"""
    assert logged_in.get("/api/settings/prompts").json() == []


def test_prompt_write_requires_permission(client: TestClient, registered) -> None:
    """viewer 既读不到 `settings:read`，更写不了 `prompt:write`。"""
    register(client, "looker")
    grant(client, "looker", "viewer")
    client.post("/api/auth/login", json={"username": "looker", "secret": secret_of("passw0rd")})

    assert client.get("/api/settings/prompts").status_code == 403
    assert (
        client.put("/api/settings/prompts/quiz.system", json={"content": "越权"}).status_code == 403
    )
