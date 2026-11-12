"""传输层三条纪律的回归。

来源：① 多账号 cookie 串号（A 账号的心跳带着 B 的 ``_uid``）；② 登录链路关键
cookie 在 302 中间跳上，漏吸收表现为"登录成功但下一个请求就 401"；
③ 提交类 POST 被重试 = 重复交卷。
"""

from __future__ import annotations

import httpx
import pytest
import respx

from chaoxing.client import ChaoxingTransport, SessionState
from chaoxing.client.exceptions import RequestBlocked
from chaoxing.client.proxy import parse_line
from chaoxing.config import ChaoxingSettings

BASE = "https://mooc1.chaoxing.com"


def _transport(**overrides: object) -> ChaoxingTransport:
    """构造一个不限速、浅重试的传输层（测试里不要真等）。"""
    params: dict[str, object] = {"request_min_interval": 0, "max_retry": 2}
    params.update(overrides)
    settings = ChaoxingSettings(**params)  # type: ignore[arg-type]
    return ChaoxingTransport(settings)


@respx.mock
async def test_two_sessions_never_share_cookies() -> None:
    """同一个 transport 服务两个账号，cookie 必须各走各的。"""
    respx.get(f"{BASE}/a").mock(return_value=httpx.Response(200, text="ok"))
    transport = _transport()
    alice = SessionState(account_id=1, cookies={"_uid": "111"})
    bob = SessionState(account_id=2, cookies={"_uid": "222"})

    await transport.request(alice, f"{BASE}/a")
    await transport.request(bob, f"{BASE}/a")

    assert "_uid=111" in respx.calls[0].request.headers["cookie"]
    assert "_uid=222" not in respx.calls[0].request.headers["cookie"]
    assert "_uid=222" in respx.calls[1].request.headers["cookie"]
    assert "_uid=111" not in respx.calls[1].request.headers["cookie"]
    await transport.aclose()


@respx.mock
async def test_cookies_from_redirect_chain_are_absorbed() -> None:
    """302 中间跳的 Set-Cookie 必须被吸收（漏了就等于丢会话）。

    真机事故（2026-10-12，测验换票）：中间跳设的 ``k8s`` **没被带回下一跳**，
    表现为 ``workHandle`` 把我们弹回登录页 ——
    所以这里不只查"进没进会话"，还要查"下一跳有没有带上"。
    """
    respx.get(f"{BASE}/start").mock(
        return_value=httpx.Response(
            302,
            headers={"location": f"{BASE}/end", "set-cookie": "mid=1; Path=/"},
        )
    )
    respx.get(f"{BASE}/end").mock(
        return_value=httpx.Response(200, text="ok", headers={"set-cookie": "final=2; Path=/"})
    )
    transport = _transport()
    session = SessionState(account_id=1)

    await transport.request(session, f"{BASE}/start")

    assert session.cookies["mid"] == "1"
    assert session.cookies["final"] == "2"
    assert "mid=1" in respx.calls[1].request.headers["cookie"]
    await transport.aclose()


@respx.mock
async def test_post_is_never_retried() -> None:
    """POST 永不自动重试 —— 重放提交请求等于重复交卷。"""
    route = respx.post(f"{BASE}/submit").mock(return_value=httpx.Response(503))
    transport = _transport()
    session = SessionState(account_id=1)

    response = await transport.request(session, f"{BASE}/submit", method="POST")

    assert response.status_code == 503
    assert route.call_count == 1
    await transport.aclose()


@respx.mock
async def test_get_retries_on_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """幂等请求遇到 5xx 要退避重试。"""
    monkeypatch.setattr("chaoxing.client.transport.asyncio.sleep", _no_sleep)
    route = respx.get(f"{BASE}/page").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, text="ok")]
    )
    transport = _transport()
    session = SessionState(account_id=1)

    response = await transport.request(session, f"{BASE}/page")

    assert response.status_code == 200
    assert route.call_count == 2
    await transport.aclose()


@respx.mock
async def test_403_is_reported_as_blocked() -> None:
    """403 要单独分类（学习通大量用 403 表达业务性拒绝，重试无意义）。"""
    respx.get(f"{BASE}/hb").mock(return_value=httpx.Response(403, text="forbidden"))
    transport = _transport()

    with pytest.raises(RequestBlocked):
        await transport.request(SessionState(account_id=1), f"{BASE}/hb")
    await transport.aclose()


@respx.mock
async def test_account_level_throttle_does_not_block_other_accounts() -> None:
    """限速是账号级的：A 账号刚请求过，不应拖慢 B 账号（否则并发退化成串行）。"""
    respx.get(f"{BASE}/x").mock(return_value=httpx.Response(200, text="ok"))
    transport = _transport(request_min_interval=5.0)

    await transport.request(SessionState(account_id=1), f"{BASE}/x")
    await transport.request(SessionState(account_id=2), f"{BASE}/x")

    assert len(respx.calls) == 2
    await transport.aclose()


async def _no_sleep(*_args: object, **_kwargs: object) -> None:
    """替身：把退避等待变成立即返回。"""
    return None


async def test_redirect_following_uses_account_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """302 逐跳跟随必须走**当前账号的客户端**（代理池接入后尤其关键）。

    早先 `_follow` 用的是直连 `self._client`：登录/换票的第一跳走代理，
    后续跳却跳出代理，出口不一致会被平台按会话异常拒绝，且代理池形同虚设。
    """
    transport = _transport()
    seen: list[tuple[str, str]] = []

    class FakeClient:
        """记录跟随请求的替身。"""

        async def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
            seen.append((method, url))
            return httpx.Response(200, text="ok", request=httpx.Request(method, url))

    class ExplodingClient:
        """直连客户端一旦被用到就失败 —— 用来证明没有走它。"""

        async def request(self, *_args: object, **_kwargs: object) -> httpx.Response:
            raise AssertionError("redirect 走到了直连 client")

    monkeypatch.setattr(transport, "_client_for", lambda _session: FakeClient())
    monkeypatch.setattr(transport, "_client", ExplodingClient())  # type: ignore[assignment]
    initial = httpx.Response(
        302,
        headers={"location": f"{BASE}/end"},
        request=httpx.Request("GET", f"{BASE}/start"),
    )

    response = await transport._follow(SessionState(account_id=1), initial)

    assert response.status_code == 200
    assert seen == [("GET", f"{BASE}/end")]


class FakePoolClient:
    """proxy_pool 替身：只记录拉取/删除次数。"""

    def __init__(self, entries: list[object]) -> None:
        self.entries = entries
        self.fetches = 0
        self.deleted: list[object] = []

    async def fetch_all(self) -> list[object]:
        self.fetches += 1
        return self.entries

    async def delete(self, entry: object) -> bool:
        self.deleted.append(entry)
        return True

    async def aclose(self) -> None:
        return None


async def test_remote_proxy_pool_refresh_is_periodic() -> None:
    """远端池按周期刷新；TTL 内的后续请求不再打代理池 API。"""
    transport = _transport()
    entries = [parse_line("http://1.2.3.4:8080")]
    assert entries[0] is not None
    fake = FakePoolClient(entries)
    transport._pool_client = fake  # type: ignore[assignment]

    await transport._ensure_proxy_pool()
    await transport._ensure_proxy_pool()

    assert fake.fetches == 1
    assert [entry.url for entry in transport._pool.entries] == ["http://1.2.3.4:8080"]
    await transport.aclose()


async def test_proxy_rotates_after_request_quota() -> None:
    """请求数到阈值就主动轮换，不等 403。"""
    transport = _transport(proxy_rotate_requests=2)
    transport._pool.sync_remote(
        [
            parse_line("http://1.2.3.4:8080"),
            parse_line("http://5.6.7.8:8080"),
        ]
    )
    session = SessionState(account_id=1)

    first = transport._client_for(session)
    transport._client_for(session)
    assert transport._active_proxy[1].url == "http://1.2.3.4:8080"
    # 请求中途不换；到单元边界才换
    assert transport._client_for(session) is first
    transport.rotate_proxy_if_due(1)
    third = transport._client_for(session)

    assert transport._active_proxy[1].url == "http://5.6.7.8:8080"
    assert third is not first
    await transport.aclose()


async def test_403_cools_locally_without_deleting_shared_exit() -> None:
    """403 是业务拒绝，不是出口坏掉的证据：只本地冷却，不能删共享远端池。"""
    transport = _transport()
    entry = parse_line("http://1.2.3.4:8080")
    assert entry is not None
    transport._pool.sync_remote([entry])
    fake = FakePoolClient([entry])
    transport._pool_client = fake  # type: ignore[assignment]
    transport._active_proxy[1] = transport._pool.entries[0]

    await transport._note_blocked(SessionState(account_id=1))

    assert not fake.deleted
    assert transport._pool.entries[0].cooling is True
    assert transport._pool_refreshed_at == 0.0
    await transport.aclose()


class FailingPoolClient(FakePoolClient):
    """模拟 proxy_pool 暂时不可用。"""

    async def fetch_all(self) -> list[object]:
        self.fetches += 1
        raise httpx.ConnectError("proxy_pool down")


async def test_remote_refresh_failure_keeps_existing_pool() -> None:
    """代理池 API 挂了要保留旧出口；否则一次抖动就把账号打成直连。"""
    transport = _transport()
    entry = parse_line("http://1.2.3.4:8080")
    assert entry is not None
    transport._pool.sync_remote([entry])
    fake = FailingPoolClient([])
    transport._pool_client = fake  # type: ignore[assignment]

    await transport._ensure_proxy_pool()

    assert fake.fetches == 1
    assert [item.url for item in transport._pool.entries] == ["http://1.2.3.4:8080"]
    assert transport._pool_refreshed_at != 0.0
    await transport.aclose()
