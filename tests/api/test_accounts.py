"""账号域：CRUD、凭据不外泄、数据可见性。

来源：计划 §5.8 的数据范围要求 —— 管理员看全部，普通用户只看自己的；
以及一条安全底线：**响应里绝不能出现凭据**（连密文都不给）。
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
    """走完整注册流程（非首个用户需要 nonce）。"""
    nonce = client.get("/api/auth/challenge", params={"scene": "register"}).json()["nonce"]
    response = client.post(
        "/api/auth/register",
        json={"username": username, "secret": secret_of(password), "nonce": nonce},
    )
    assert response.status_code == 201, response.text


async def _grant(client: TestClient, username: str, role: str) -> None:
    """直接经服务层授权（``/users`` 路由是下一阶段的事）。"""
    container: Container = client.app.state.container  # type: ignore[attr-defined]
    async with container.uow.read() as session:
        from sqlalchemy import select

        from chaoxing.storage.models import User

        user = (await session.scalars(select(User).where(User.username == username))).first()
    assert user is not None
    await container.users.grant(user.id, role)


def grant(client: TestClient, username: str, role: str) -> None:
    """同步包装（测试里直接用）。"""
    asyncio.run(_grant(client, username, role))


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    """已登录的超管客户端。"""
    register(client, "admin")
    assert (
        client.post(
            "/api/auth/login", json={"username": "admin", "secret": secret_of("passw0rd")}
        ).status_code
        == 200
    )
    return client


@pytest.fixture
def bob(admin: TestClient) -> Iterator[TestClient]:
    """普通用户客户端。

    **必须依赖 ``admin``**：否则当 bob 是库里第一个用户时，他会自动成为超管，
    于是"普通用户"的用例全部失去意义（而它们恰恰是数据可见性的把关者）。
    用自己的 ``TestClient`` 是为了持有一套独立的 Cookie（两个会话不能互相污染）。
    """
    from starlette.testclient import TestClient as StarletteClient

    with StarletteClient(admin.app) as peer:
        register(peer, "bob", "bob-pass")
        grant(peer, "bob", "viewer")
        assert (
            peer.post(
                "/api/auth/login", json={"username": "bob", "secret": secret_of("bob-pass")}
            ).status_code
            == 200
        )
        yield peer


def test_create_account_hides_credentials(admin: TestClient) -> None:
    """创建账号：响应里只有"有没有密码"，**没有**任何凭据字段。"""
    response = admin.post(
        "/api/accounts", json={"username": "13800000000", "password": "super-secret"}
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["has_password"] is True
    raw = response.text
    assert "super-secret" not in raw
    assert "password_enc" not in raw
    assert "cookie_enc" not in raw


def test_duplicate_username_conflicts(admin: TestClient) -> None:
    """同一超星账号不能重复录入。"""
    admin.post("/api/accounts", json={"username": "13800000000"})

    response = admin.post("/api/accounts", json={"username": "13800000000"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "account_exists"


def test_admin_sees_all_accounts(admin: TestClient) -> None:
    """管理员（account:read:all）能看到自己建的账号。"""
    admin.post("/api/accounts", json={"username": "13800000001"})

    response = admin.get("/api/accounts")

    assert response.status_code == 200
    assert [item["username"] for item in response.json()] == ["13800000001"]


def test_normal_user_does_not_see_others_accounts(admin: TestClient, bob: TestClient) -> None:
    """普通用户的列表里**不能出现**别人的账号（数据可见性的核心）。"""
    admin.post("/api/accounts", json={"username": "13800000002"})

    response = bob.get("/api/accounts")

    assert response.status_code == 200
    assert response.json() == []


def test_normal_user_gets_404_for_foreign_account(admin: TestClient, bob: TestClient) -> None:
    """访问别人的账号返回 404 而不是 403 —— 403 等于确认"这个 ID 存在"。"""
    created = admin.post("/api/accounts", json={"username": "13800000003"}).json()

    response = bob.get(f"/api/accounts/{created['id']}")

    assert response.status_code == 404


def test_normal_user_cannot_write_accounts(bob: TestClient) -> None:
    """viewer 角色没有写权限。"""
    response = bob.post("/api/accounts", json={"username": "13800000004"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_update_keeps_other_fields(admin: TestClient) -> None:
    """PATCH 只改传进来的字段（没传的保持原样）。"""
    created = admin.post(
        "/api/accounts", json={"username": "13800000005", "note": "原备注", "priority": 50}
    ).json()

    response = admin.patch(f"/api/accounts/{created['id']}", json={"note": "新备注"})

    assert response.status_code == 200
    body = response.json()
    assert body["note"] == "新备注"
    assert body["priority"] == 50


def test_delete_account(admin: TestClient) -> None:
    """删除后列表里不再出现。"""
    created = admin.post("/api/accounts", json={"username": "13800000006"}).json()

    assert admin.delete(f"/api/accounts/{created['id']}").status_code == 200
    assert admin.get("/api/accounts").json() == []


def test_group_filter_does_not_relax_visibility(admin: TestClient, bob: TestClient) -> None:
    """**按组收窄不能放松可见性**：两个过滤条件是叠加的，不是二选一。

    组是"批量操作与共享的入口"，但它不该成为越权通道 —— 普通用户拿同一个
    group_id 去查，仍然只看得到自己名下的账号。
    """
    group = admin.post("/api/groups", json={"name": "机房A"}).json()
    account = admin.post("/api/accounts", json={"username": "13800000007"}).json()
    added = admin.post(f"/api/groups/{group['id']}/members", json={"account_ids": [account["id"]]})
    assert added.json()["changed"] == 1

    # 管理员按组查 → 看得到
    assert len(admin.get("/api/accounts", params={"group_id": group["id"]}).json()) == 1
    # 普通用户按同一个组查 → 看不到（组不放松归属）
    assert bob.get("/api/accounts", params={"group_id": group["id"]}).json() == []


def test_viewer_cannot_touch_groups(bob: TestClient) -> None:
    """viewer 既不能建组、也不能改成员 —— 组是共享入口，不该对只读角色开放。

    （"把别人的账号塞进组"那种越权场景需要 ``group:write`` 才谈得上，
    因此这里守的是更前一层的边界：这个角色根本没有动组的权限。）
    """
    assert bob.post("/api/groups", json={"name": "x"}).status_code == 403
    assert bob.get("/api/groups").status_code == 403


def test_stats_requires_account_for_normal_user(bob: TestClient) -> None:
    """普通用户不指定账号看统计 → 拒绝（否则"不传参数"就把全库合计送出去了）。"""
    response = bob.get("/api/stats/kpi")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "account_required"


def test_admin_can_see_global_stats(admin: TestClient) -> None:
    """管理员不指定账号时看全局。"""
    response = admin.get("/api/stats/kpi")

    assert response.status_code == 200
    assert "points_total" in response.json()
