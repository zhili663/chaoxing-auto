"""检索层：作答前可选的外部资料，**只有这个文件**认识检索细节。

用 DuckDuckGo 的 HTML 端点（`html.duckduckgo.com/html/`）—— 不需要 Key，
代价是返回的是要解析的页面而不是 JSON。检索本身是**尽力而为**：
失败、超时、空结果都不影响模型作答（调用方只记日志）。

换检索源（Bing 等）只改这里；上层只认 :class:`SearchProvider` 协议。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

#: 单次检索返回给模型的最大片段数（再多的资料是噪声，还费 token）
SNIPPET_LIMIT = 3
#: DDG HTML 端点（无 Key）
DDG_ENDPOINT = "https://html.duckduckgo.com/html/"
#: 不带 UA 会被端点直接拒绝
DDG_USER_AGENT = "Mozilla/5.0 (compatible; chaoxing-auto/1.0; +https://example.invalid)"
#: 检索是辅助，慢过这个数就不值得再等（超时必须短于模型超时）
DDG_TIMEOUT = 2.0


@dataclass(frozen=True, slots=True)
class Snippet:
    """一条检索结果。"""

    title: str
    url: str
    text: str

    @property
    def domain(self) -> str:
        """来源域名（提示词里标注来源用，不暴露完整链接）。"""
        return urlparse(self.url).netloc


class SearchProvider(Protocol):
    """检索契约（换检索源实现它即可）。"""

    async def search(self, query: str, *, limit: int) -> list[Snippet]:
        """检索一段文本。

        :param query: 查询词（通常是题干）
        :param limit: 最多返回几条
        :returns: 片段列表；查不到时返回空列表
        """
        ...


class DuckDuckGoSearch:
    """DuckDuckGo HTML 端点实现（无 Key）。

    :param endpoint: 端点地址（测试里可指向本地）
    :param timeout: 单次请求超时（秒）
    :param user_agent: 请求头 UA
    """

    def __init__(
        self,
        *,
        endpoint: str = DDG_ENDPOINT,
        timeout: float = DDG_TIMEOUT,
        user_agent: str = DDG_USER_AGENT,
    ) -> None:
        self._endpoint = endpoint
        self._timeout = timeout
        self._headers = {"User-Agent": user_agent}

    async def search(self, query: str, *, limit: int) -> list[Snippet]:
        """检索并解析 HTML 结果页。

        :param query: 查询词
        :param limit: 最多返回几条
        :raises httpx.HTTPError: 网络异常 / 超时（由调用方决定是否降级）
        :returns: 片段列表
        """
        async with httpx.AsyncClient(
            timeout=self._timeout, headers=self._headers, follow_redirects=True
        ) as client:
            response = await client.get(self._endpoint, params={"q": query})
            response.raise_for_status()
        return _parse_html(response.text, limit=max(0, limit))


class FakeSearch:
    """离线 / 测试用检索：返回预置片段，可制造失败。"""

    def __init__(self, snippets: list[Snippet] | None = None, *, error: Exception | None = None):
        """初始化。

        :param snippets: 预置片段（按顺序返回，超出 ``limit`` 的部分截掉）
        :param error: 非空则每次调用都抛它（模拟检索挂了）
        """
        self._snippets = list(snippets or [])
        self._error = error
        #: 每次调用记录 ``(query, limit)``，断言"是否真的检索了"
        self.calls: list[tuple[str, int]] = []

    async def search(self, query: str, *, limit: int) -> list[Snippet]:
        """按预置数据回答。

        :param query: 查询词
        :param limit: 最多返回几条
        :raises Exception: 构造时传了 ``error``
        :returns: 片段列表
        """
        self.calls.append((query, limit))
        if self._error is not None:
            raise self._error
        return self._snippets[: max(0, limit)]


def _parse_html(html: str, *, limit: int) -> list[Snippet]:
    """从 DDG HTML 结果页里抽出片段。

    选择器：每条结果在 ``div.result`` 里，标题是 ``a.result__a``、
    摘要是 ``.result__snippet``；链接可能是 ``/l/?uddg=…`` 的跳转壳，要还原。

    :param html: 结果页 HTML
    :param limit: 最多解析几条
    :returns: 片段列表
    """
    soup = BeautifulSoup(html, "lxml")
    snippets: list[Snippet] = []
    for node in soup.select("div.result"):
        link = node.select_one("a.result__a")
        if link is None:
            continue
        snippet = node.select_one(".result__snippet")
        snippets.append(
            Snippet(
                title=link.get_text(" ", strip=True),
                url=_real_url(str(link.get("href", ""))),
                text=snippet.get_text(" ", strip=True) if snippet is not None else "",
            )
        )
        if len(snippets) >= limit:
            break
    return snippets


def _real_url(href: str) -> str:
    """把 DDG 的跳转壳还原成真实 URL。

    :param href: ``a.result__a`` 的 href
    :returns: 真实 URL（无法还原时原样返回）
    """
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return unquote(target[0])
    return href


__all__ = [
    "DDG_ENDPOINT",
    "DDG_TIMEOUT",
    "SNIPPET_LIMIT",
    "DuckDuckGoSearch",
    "FakeSearch",
    "SearchProvider",
    "Snippet",
]
