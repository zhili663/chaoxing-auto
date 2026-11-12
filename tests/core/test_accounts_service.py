"""账号服务：登录编排、会话落库、事件发布。

用 respx 复刻真实登录链路（登录页 → 提交 → 门户页校验），断言的重点是
**编排行为**：会话是否加密落库、事件是否发出、重启后能否恢复。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from sqlalchemy import select

from chaoxing.client import ChaoxingTransport, SessionRegistry
from chaoxing.config import ChaoxingSettings
from chaoxing.core.services import AccountService
from chaoxing.obs.events import AccountLoggedIn, EventBus, HumanRequired, SessionExpired
from chaoxing.storage.models import Account
from chaoxing.storage.session_store import DatabaseSessionStore
from chaoxing.storage.uow import UnitOfWork
from chaoxing.utils.crypto import SecretBox

PASSPORT = "https://passport2.chaoxing.com"
PORTAL = "https://i.chaoxing.com"


@pytest.fixture
def box() -> SecretBox:
    """固定密钥（测试间不共享状态）。"""
    return SecretBox(SecretBox.generate_key())


@pytest.fixture
async def bus() -> AsyncIterator[EventBus]:
    """事件总线（用完关掉，避免悬挂任务）。"""
    instance = EventBus()
    yield instance
    await instance.close()


@pytest.fixture
def service(
    uow: UnitOfWork, box: SecretBox, bus: EventBus
) -> tuple[AccountService, ChaoxingTransport]:
    """账号服务 + 传输层（测试结束时关闭传输层）。"""
    transport = ChaoxingTransport(ChaoxingSettings(request_min_interval=0, max_retry=0))
    registry = SessionRegistry(store=DatabaseSessionStore(uow, box))
    return (
        AccountService(uow=uow, transport=transport, registry=registry, box=box, bus=bus),
        transport,
    )


def mock_login_success(*, captcha: bool = False) -> None:
    """复刻一次成功登录的三个请求。"""
    respx.get(f"{PASSPORT}/login").mock(return_value=httpx.Response(200, text="<html></html>"))
    body = (
        '{"msg2": "请输入验证码", "status": false}' if captcha else '{"status": true, "msg": "ok"}'
    )
    respx.post(f"{PASSPORT}/fanyalogin").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/html"}, text=body)
    )
    respx.get(f"{PORTAL}/base").mock(
        return_value=httpx.Response(
            200, text="portal", headers={"set-cookie": "_uid=100000005; Path=/"}
        )
    )


@respx.mock
async def test_login_persists_encrypted_session_and_emits_event(
    uow: UnitOfWork, account_id: int, box: SecretBox, bus: EventBus, service
) -> None:
    """登录成功：cookie 加密落库、状态变 logged_in、事件发出。"""
    account_service, transport = service
    events: list[object] = []

    async def collector(event: object) -> None:
        events.append(event)

    bus.subscribe(collector)
    mock_login_success()

    result = await account_service.login(account_id, password="secret")

    assert result.ok is True
    await bus.wait_idle(timeout=1.0)
    assert any(isinstance(event, AccountLoggedIn) for event in events)

    async with uow.read() as session:
        account = (await session.scalars(select(Account))).one()
        assert account.status == "logged_in"
        assert account.cookie_enc  # 加密后的会话快照
        snapshot = box.decrypt_json(account.cookie_enc)
        assert snapshot["cookies"]["_uid"] == "100000005"

    await transport.aclose()


@respx.mock
async def test_login_with_password_stores_it_encrypted(
    uow: UnitOfWork, account_id: int, box: SecretBox, service
) -> None:
    """提供了新密码就加密落库（这也是"明文密码转加密"的修复路径）。"""
    account_service, transport = service
    mock_login_success()

    await account_service.login(account_id, password="brand-new")

    async with uow.read() as session:
        account = (await session.scalars(select(Account))).one()
        assert account.password_enc
        assert account.password_enc != "brand-new"
        assert box.decrypt(account.password_enc) == "brand-new"

    await transport.aclose()


@respx.mock
async def test_login_without_password_reports_cleanly(
    uow: UnitOfWork, account_id: int, service
) -> None:
    """库里没密码又不给密码时，给出可行动的提示而不是抛异常。"""
    account_service, transport = service

    result = await account_service.login(account_id)

    assert result.ok is False
    assert "密码" in result.message
    await transport.aclose()


@respx.mock
async def test_captcha_flow_emits_human_required(account_id: int, bus: EventBus, service) -> None:
    """服务端索要验证码时发出"需要人工"事件。"""
    account_service, transport = service
    events: list[object] = []

    async def collector(event: object) -> None:
        events.append(event)

    bus.subscribe(collector)
    mock_login_success(captcha=True)

    result = await account_service.login(account_id, password="secret")

    assert result.need_human is True
    await bus.wait_idle(timeout=1.0)
    assert any(isinstance(event, HumanRequired) for event in events)
    await transport.aclose()


@respx.mock
async def test_session_survives_restart(
    uow: UnitOfWork, account_id: int, box: SecretBox, service
) -> None:
    """重启后能从库里恢复会话（这是"不必每次重新登录"的关键）。"""
    account_service, transport = service
    mock_login_success()
    await account_service.login(account_id, password="secret")
    await transport.aclose()

    # 模拟重启：新建注册表，从库恢复
    fresh_registry = SessionRegistry(store=DatabaseSessionStore(uow, box))
    restored = await fresh_registry.restore()

    assert restored == 1
    assert fresh_registry.get(account_id).cookies["_uid"] == "100000005"


@respx.mock
async def test_logout_clears_local_state(uow: UnitOfWork, account_id: int, service) -> None:
    """登出只清本地态（不动学习通侧会话），状态回到 pending。"""
    account_service, transport = service
    mock_login_success()
    await account_service.login(account_id, password="secret")

    await account_service.logout(account_id)

    async with uow.read() as session:
        account = (await session.scalars(select(Account))).one()
        assert account.status == "pending"
    await transport.aclose()


@respx.mock
async def test_refresh_marks_expired_and_emits_event(
    uow: UnitOfWork, account_id: int, bus: EventBus, box: SecretBox, service
) -> None:
    """登录态失效时标记 expired 并发出事件。"""
    account_service, transport = service
    mock_login_success()
    await account_service.login(account_id, password="secret")

    # 让下一次校验被重定向回登录页
    respx.get(f"{PORTAL}/base").mock(
        return_value=httpx.Response(302, headers={"location": f"{PASSPORT}/login?refer=base"})
    )
    events: list[object] = []

    async def collector(event: object) -> None:
        events.append(event)

    bus.subscribe(collector)

    assert await account_service.refresh(account_id) is False

    await bus.wait_idle(timeout=1.0)
    assert any(isinstance(event, SessionExpired) for event in events)
    async with uow.read() as session:
        account = (await session.scalars(select(Account))).one()
        assert account.status == "expired"
    await transport.aclose()
