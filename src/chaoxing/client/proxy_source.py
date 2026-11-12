"""``jhao104/proxy_pool`` sidecar 的 HTTP 客户端。

为什么单独一层：proxy_pool 是 Flask + Redis 的独立服务，不是可 import 的插件。
本项目只通过它的 HTTP API 取快照/删坏出口，业务层不感知它对 Redis 的依赖。

API 契约（proxy_pool ``api/proxyApi.py``）：

* ``GET /all/?type=https`` → ``[{"proxy": "ip:port", "https": true, ...}]``
* ``GET /delete/?proxy=ip:port`` → 删除一个坏出口
* ``GET /count/`` → 池统计

注意：``type=https`` 表示该代理**支持** HTTPS CONNECT；httpx 的 ``proxy=``
通常仍应写 ``http://ip:port``（TLS-to-proxy 是另一回事）。因此这里把
API 误带出的 ``https://`` 归一成 ``http://`` —— 免费池几乎都是普通 HTTP 代理。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from chaoxing.client.proxy import ProxyEntry, parse_line

log = logging.getLogger(__name__)


def parse_remote_proxy(raw: str) -> ProxyEntry | None:
    """把 proxy_pool 的 ``proxy`` 字段解析成 :class:`ProxyEntry`。

    :param raw: ``ip:port`` / ``user:pass@ip:port``（也可能带协议前缀）
    :returns: 出口；无法解析返回 ``None``
    """
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("https://"):
        # 见模块 docstring：支持 HTTPS 的普通代理不等于 TLS-to-proxy。
        text = "http://" + text[len("https://") :]
    elif "://" not in text:
        text = "http://" + text
    return parse_line(text)


class ProxyPoolClient:
    """访问 proxy_pool sidecar API 的异步客户端（**不经过**业务代理）。

    :param base_url: 例如 ``http://127.0.0.1:5010``
    :param timeout: 连接/读取超时（秒）
    :param client: 注入的 httpx 客户端（测试用；注入了就不由本类关闭）
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )
        self._owns_client = client is None

    async def fetch_all(self) -> list[ProxyEntry]:
        """拉取远端当前的全部 HTTPS 代理。

        :raises httpx.HTTPError: 网络失败、非 2xx（由传输层决定是否保留旧池）
        :returns: 去重后的出口列表；远端空池返回 ``[]``
        """
        response = await self._client.get(f"{self.base_url}/all/", params={"type": "https"})
        response.raise_for_status()
        data: Any = response.json()
        if not isinstance(data, list):
            log.warning("proxy_pool /all/ 返回了非列表：%r", data)
            return []

        entries: list[ProxyEntry] = []
        seen: set[str] = set()
        for item in data:
            if not isinstance(item, dict):
                continue
            # 已限定 type=https；字段明确为 false 的仍跳过（防服务端忽略过滤参数）。
            if item.get("https") is False:
                continue
            entry = parse_remote_proxy(str(item.get("proxy") or ""))
            if entry is None or entry.url in seen:
                continue
            seen.add(entry.url)
            entries.append(entry)
        return entries

    async def delete(self, entry: ProxyEntry) -> bool:
        """从远端池删除一个出口（失败只记日志，不影响本地冷却）。

        :param entry: 本地出口
        :returns: 远端确认成功为真
        """
        raw = entry.url.split("://", 1)[-1]
        try:
            response = await self._client.get(f"{self.base_url}/delete/", params={"proxy": raw})
            response.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            log.warning("proxy_pool 删除出口 %s 失败：%s", entry.label, exc)
            return False

    async def count(self) -> dict[str, Any]:
        """取远端池统计（诊断/CLI 用）。"""
        response = await self._client.get(f"{self.base_url}/count/")
        response.raise_for_status()
        data: Any = response.json()
        return data if isinstance(data, dict) else {}

    async def aclose(self) -> None:
        """关闭自有客户端（注入的由调用方负责）。"""
        if self._owns_client:
            await self._client.aclose()
