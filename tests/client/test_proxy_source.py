"""``proxy_pool`` sidecar 适配层：只验证 HTTP 契约与归一化，不依赖真实 Redis。"""

from __future__ import annotations

import httpx

from chaoxing.client.proxy import parse_line
from chaoxing.client.proxy_source import ProxyPoolClient, parse_remote_proxy


async def test_fetch_all_filters_and_normalizes() -> None:
    """``/all/?type=https``：只收 https=true，``https://`` 要归一成普通 HTTP 代理。"""
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["type"] = request.url.params.get("type")
        return httpx.Response(
            200,
            json=[
                {"proxy": "1.2.3.4:8080", "https": True},
                {"proxy": "5.6.7.8:3128", "https": False},
                {"proxy": "https://9.9.9.9:8080", "https": True},
                {"proxy": "1.2.3.4:8080", "https": True},
                {"proxy": "not-a-proxy", "https": True},
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    source = ProxyPoolClient("http://pool.local:5010/", client=client)

    entries = await source.fetch_all()

    assert seen == {"path": "/all/", "type": "https"}
    assert [entry.url for entry in entries] == [
        "http://1.2.3.4:8080",
        "http://9.9.9.9:8080",
    ]
    await client.aclose()


async def test_delete_sends_bare_host_port_with_auth() -> None:
    """``/delete/?proxy=`` 要 ``host:port``（有认证时保留认证串），不能带协议。"""
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["proxy"] = request.url.params.get("proxy")
        return httpx.Response(200, json={"code": 0, "src": "ok"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    source = ProxyPoolClient("http://pool.local:5010", client=client)
    entry = parse_line("http://alice:secret@1.2.3.4:8080")
    assert entry is not None

    assert await source.delete(entry) is True
    assert seen == {"path": "/delete/", "proxy": "alice:secret@1.2.3.4:8080"}
    await client.aclose()


def test_parse_remote_proxy_handles_common_shapes() -> None:
    """proxy_pool 的 ``proxy`` 字段可能是裸 ip:port 或带认证串。"""
    assert parse_remote_proxy("1.2.3.4:8080") is not None
    assert parse_remote_proxy("1.2.3.4:8080").url == "http://1.2.3.4:8080"  # type: ignore[union-attr]
    assert parse_remote_proxy("u:p@1.2.3.4:8080").url == "http://u:p@1.2.3.4:8080"  # type: ignore[union-attr]
    assert parse_remote_proxy("https://1.2.3.4:8080").url == "http://1.2.3.4:8080"  # type: ignore[union-attr]
    assert parse_remote_proxy("") is None
