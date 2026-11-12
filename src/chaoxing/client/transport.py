"""全局唯一的 HTTP 传输层：连接池、账号级限速、重试、代理。

三条纪律（全部由真实事故换来）：

1. **不设 client 级 cookie jar** —— 一个 client 服务多个账号时，client 级 cookie
   会互相覆盖导致串号；cookie 一律由 :class:`~chaoxing.client.session.SessionState`
   持有并按请求传入；
2. **限速是账号级的** —— 全局限速会让"3 个账号并发"退化成串行；
3. **POST 永不自动重试** —— 提交测验被重放就等于重复交卷。

错误分类：403 抛 :class:`~chaoxing.client.exceptions.RequestBlocked`（学习通常用它表达
"参数不对"，需要调用方判断），网络层失败抛
:class:`~chaoxing.client.exceptions.TransportError`。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from types import TracebackType
from typing import Any
from urllib.parse import urljoin

import httpx

from chaoxing.client.constants import COMMON_HEADERS
from chaoxing.client.endpoints.base import Endpoint
from chaoxing.client.exceptions import RequestBlocked, TransportError
from chaoxing.client.proxy import ProxyEntry, ProxyPool
from chaoxing.client.proxy_source import ProxyPoolClient
from chaoxing.client.session import SessionState
from chaoxing.config import ChaoxingSettings

#: 代理池文件的默认相对路径（相对进程工作目录，与 `cx proxy` 的默认一致）
PROXY_POOL_FILENAME = "data/proxies.txt"

log = logging.getLogger(__name__)

#: 只有幂等请求才允许自动重试
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
#: 值得重试的状态码
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
#: 被拒绝（学习通大量使用 403 表达业务性拒绝）
BLOCKED_STATUS = frozenset({403})
#: 手工跟随跳转的跳数上限（防跳转环）
MAX_REDIRECTS = 5
#: 退避上限（秒）
MAX_BACKOFF = 8.0


class ChaoxingTransport:
    """账号共享的 HTTP 客户端。

    :param settings: 学习通域配置（超时、限速、重试、代理）
    :param client: 可注入的 httpx 客户端（测试用 respx / MockTransport 替换）
    """

    def __init__(
        self,
        settings: ChaoxingSettings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.request_timeout),
            proxy=settings.proxy or None,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
            headers=COMMON_HEADERS,
        )
        self._owns_client = client is None
        #: 注入的客户端（测试用）—— 有它就不走代理池
        self._injected = client
        #: 代理池（空池 = 全直连；文件里的每一行一个出口）
        self._pool = ProxyPool.from_file(
            settings.proxy_pool_file or PROXY_POOL_FILENAME,
            cooldown_s=settings.proxy_cooldown_s,
        )
        #: proxy_pool sidecar（HTTP API；为空 = 只用文件直连池）
        self._pool_client = (
            ProxyPoolClient(settings.proxy_pool_api, timeout=settings.proxy_pool_timeout)
            if settings.proxy_pool_api
            else None
        )
        #: 上次成功/尝试刷新远端池的时间（monotonic）
        self._pool_refreshed_at = 0.0
        self._pool_refresh_lock = asyncio.Lock()
        #: 每个出口一个客户端（httpx 的代理是客户端级的，不能单请求换）
        self._proxy_clients: dict[str, httpx.AsyncClient] = {}
        #: 账号 -> 本次用的出口（命中 403 时据此冷却该出口）
        self._active_proxy: dict[int, ProxyEntry] = {}
        self._last_request_at: dict[int, float] = {}
        #: 账号 -> 当前出口已服务的请求数 / 绑定时刻（主动轮换用）
        self._proxy_request_count: dict[int, int] = {}
        self._proxy_bound_at: dict[int, float] = {}

    async def _ensure_proxy_pool(self) -> None:
        """按周期从 proxy_pool sidecar 拉取出口快照（失败保留旧池）。

        只在真正的请求路径上调用（``request`` / ``_follow``）；每账号请求都会
        经过它，但只有超过 ``proxy_pool_refresh_s`` 才发一次 API 请求。

        失败语义：**保留旧池**继续跑，不因为代理池挂了就让账号直连或停摆；
        30 秒后重试（免费池常见短暂不可用，等满一个周期太久）。
        """
        if self._pool_client is None or self._injected is not None:
            return
        now = time.monotonic()
        interval = self._settings.proxy_pool_refresh_s
        if self._pool_refreshed_at and now - self._pool_refreshed_at < interval:
            return

        async with self._pool_refresh_lock:
            now = time.monotonic()
            if self._pool_refreshed_at and now - self._pool_refreshed_at < interval:
                return
            try:
                entries = await self._pool_client.fetch_all()
            except Exception as exc:
                retry_after = min(30.0, interval)
                self._pool_refreshed_at = now - interval + retry_after
                log.warning(
                    "代理池刷新失败（%s），保留现有 %d 条，%.0fs 后重试",
                    exc,
                    len(self._pool.entries),
                    retry_after,
                )
                return

            if entries:
                self._pool.sync_remote(entries)
                # 远端快照会丢掉已下线的出口；它们对应的 httpx client 需要关闭，
                # 否则长跑进程会按 120s 的刷新节奏累积 keep-alive 连接。
                keep = {entry.url for entry in self._pool.entries}
                keep.update(entry.url for entry in self._active_proxy.values())
                for url in list(self._proxy_clients):
                    if url in keep:
                        continue
                    await self._proxy_clients.pop(url).aclose()
                log.info("代理池已刷新：%d 条出口", len(entries))
            else:
                # 远端暂时空池：保留文件/上一次快照，避免好代理被一次空响应清空；
                # 30 秒后再试（免费池刚启动时经常先是空池）。
                log.warning("代理池返回空列表，保留现有 %d 条出口", len(self._pool.entries))
                self._pool_refreshed_at = now - interval + min(30.0, interval)
                return
            self._pool_refreshed_at = time.monotonic()

    def rotate_proxy(self, account_id: int) -> ProxyEntry | None:
        """主动换一个出口；池空/只有一条/全部冷却时无实际变化。

        在账号开始运行时调用，确保新 run 不沿用上一个 run 的出口；真正的
        请求级阈值在 :meth:`_maybe_rotate_proxy` 中检查。
        """
        if self._injected is not None or not self._pool.enabled:
            return None
        entry = self._pool.rotate(str(account_id))
        self._proxy_request_count[account_id] = 0
        self._proxy_bound_at[account_id] = time.monotonic()
        if entry is not None:
            log.info("账号 %s 主动轮换出口：%s", account_id, entry.label)
        return entry

    def rotate_proxy_if_due(self, account_id: int) -> None:
        """在**安全边界**（任务点/播放单元之间）按请求数/粘性时长主动轮换。

        刻意不在 ``_client_for`` 里切：视频心跳中途换 IP 可能让 dtoken/会话上下文
        对不上，反而更像异常。调用方只在单元边界调用它。
        """
        if self._injected is not None or not self._pool.enabled:
            return
        now = time.monotonic()
        bound_at = self._proxy_bound_at.setdefault(account_id, now)
        count = self._proxy_request_count.get(account_id, 0)
        by_count = (
            self._settings.proxy_rotate_requests > 0
            and count >= self._settings.proxy_rotate_requests
        )
        by_time = (
            self._settings.proxy_rotate_seconds > 0
            and now - bound_at >= self._settings.proxy_rotate_seconds
        )
        if by_count or by_time:
            self.rotate_proxy(account_id)

    def _client_for(self, session: SessionState) -> httpx.AsyncClient:
        """取该账号这一请求要用的客户端（同账号粘性；池空或全冷却则直连）。"""
        if self._injected is not None or not self._pool.enabled:
            return self._client

        entry = self._pool.pick(str(session.account_id))
        if entry is None:
            self._active_proxy.pop(session.account_id, None)
            self._proxy_request_count.pop(session.account_id, None)
            self._proxy_bound_at.pop(session.account_id, None)
            return self._client

        previous = self._active_proxy.get(session.account_id)
        if previous is not None and previous.url != entry.url:
            # 403/远端刷新已经换过出口：计数不能沿用到新 IP，否则下个边界
            # 会立刻把刚拿到的出口再轮换掉。
            self._proxy_request_count[session.account_id] = 0
            self._proxy_bound_at[session.account_id] = time.monotonic()
        self._proxy_request_count[session.account_id] = (
            self._proxy_request_count.get(session.account_id, 0) + 1
        )
        self._proxy_bound_at.setdefault(session.account_id, time.monotonic())
        client = self._proxy_clients.get(entry.url)
        if client is None:
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._settings.request_timeout),
                proxy=entry.url,
                follow_redirects=True,
                limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
                headers=COMMON_HEADERS,
            )
            self._proxy_clients[entry.url] = client
        self._active_proxy[session.account_id] = entry
        return client

    async def _note_blocked(self, session: SessionState) -> None:
        """被拒（403）时冷却本地出口，并通知 proxy_pool 删除它。

        刻意**不重放**本次请求：失败即换 IP 重试正是请求风暴的形态。
        """
        entry = self._active_proxy.pop(session.account_id, None)
        self._proxy_request_count.pop(session.account_id, None)
        self._proxy_bound_at.pop(session.account_id, None)
        if entry is None:
            return
        self._pool.mark_blocked(entry, reason="403")
        # 403 在本项目里大量是**业务拒绝**（ktoken/enc 过期、参数错误），
        # 不是出口坏掉的证据；不能拿它删除共享远端池里的健康出口。
        # 只做本地冷却；下次请求会换一个出口，冷却到期后再回来。
        if self._pool_client is not None:
            self._pool_refreshed_at = 0.0
        log.warning(
            "出口 %s 被拒，已冷却 %.0fs（账号 %s 下次自动换出口）",
            entry.label,
            self._pool.cooldown_s,
            session.account_id,
        )

    def proxy_stats(self) -> list[dict[str, object]]:
        """代理池概览（给 CLI/UI 用；不含口令）。"""
        return self._pool.stats()

    # ------------------------------------------------------------------ #
    # 属性
    # ------------------------------------------------------------------ #
    @property
    def client(self) -> httpx.AsyncClient:
        """底层 httpx 客户端（供高级用法与测试断言）。"""
        return self._client

    @property
    def settings(self) -> ChaoxingSettings:
        """当前生效的学习通域配置。"""
        return self._settings

    # ------------------------------------------------------------------ #
    # 主流程
    # ------------------------------------------------------------------ #
    async def request(
        self,
        session: SessionState,
        target: Endpoint | str,
        *,
        method: str | None = None,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        follow_redirects: bool | None = None,
    ) -> httpx.Response:
        """发起一次请求（自动带上会话 cookie 与账号级限速）。

        :param session: 账号会话
        :param target: 端点描述或直接给 URL
        :param method: 覆盖端点默认方法
        :param params: 查询参数
        :param data: 表单参数
        :param headers: 额外请求头（覆盖默认）
        :param follow_redirects: 是否跟随跳转（默认 True）
        :raises RequestBlocked: 服务端返回 403
        :raises TransportError: 网络失败或重试耗尽
        :returns: httpx 响应（cookie 已被 session 吸收）
        """
        spec = target if isinstance(target, Endpoint) else None
        url = spec.url if spec is not None else str(target)
        http_method = (method or (spec.method if spec is not None else "GET")).upper()
        attempts = self._settings.max_retry + 1 if http_method in IDEMPOTENT_METHODS else 1
        redirect = True if follow_redirects is None else follow_redirects

        await self._ensure_proxy_pool()

        last_error: Exception | None = None
        for attempt in range(attempts):
            await self._throttle(session.account_id)
            session.touch()
            try:
                response = await self._client_for(session).request(
                    http_method,
                    url,
                    params=params,
                    data=data,
                    headers=session.headers(dict(headers or {})),
                    follow_redirects=False,
                )
                if redirect:
                    response = await self._follow(session, response, headers=headers)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    raise TransportError(f"{http_method} {url} 网络失败：{exc}") from exc
                log.warning("%s %s 网络异常（第 %d 次），退避重试", http_method, url, attempt + 1)
                await self._backoff(attempt)
                continue

            session.absorb(response)

            if response.status_code in BLOCKED_STATUS:
                # 403 = 被拒：把该账号正在用的出口冷却，下次自动换出口（不重放本次请求）
                await self._note_blocked(session)
                raise RequestBlocked(
                    f"{http_method} {url} 被拒绝（HTTP 403）：{response.text[:200]}",
                    payload={
                        "status": response.status_code,
                        "url": str(response.url),
                        "body": response.text[:2000],
                    },
                )

            if response.status_code in RETRY_STATUS and attempt + 1 < attempts:
                log.warning(
                    "%s %s 返回 %d（第 %d 次），退避重试",
                    http_method,
                    url,
                    response.status_code,
                    attempt + 1,
                )
                await self._backoff(attempt)
                continue

            return response

        raise TransportError(f"{http_method} {url} 重试耗尽") from last_error

    async def _follow(
        self,
        session: SessionState,
        response: httpx.Response,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """手工跟随跳转：**每一跳先吸收 Set-Cookie，再发下一跳**。

        为什么不用 ``follow_redirects=True``（真机实测 2026-10-12，测验换票）：
        cookie 由我们按请求放进头里，httpx 自动跟随时只在**发第一跳之前**取一次 jar，
        第二跳仍带着那份旧 Cookie —— 而学习通把关键 cookie 设在 302 上
        （``api/work`` 跳 1 设 ``k8s``，跳 2 少了它就被弹回登录页，
        表现为"换票后未落到题目页"，而浏览器同一 URL 一切正常）。
        """
        hops = 0
        while response.is_redirect and hops < MAX_REDIRECTS:
            await self._ensure_proxy_pool()
            location = str(response.headers.get("location", "") or "")
            if not location:
                break
            target = urljoin(str(response.url), location)
            session.absorb(response)
            await self._throttle(session.account_id)
            session.touch()
            response = await self._client_for(session).request(
                "GET",
                target,
                headers=session.headers(dict(headers or {})),
                follow_redirects=False,
            )
            hops += 1
        session.absorb(response)
        return response

    # ------------------------------------------------------------------ #
    # 限速与退避
    # ------------------------------------------------------------------ #
    async def _throttle(self, account_id: int) -> None:
        """账号级最小间隔限速（**不是全局**，多账号之间互不拖累）。"""
        interval = self._settings.request_min_interval
        if interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at.get(account_id, 0.0)
        if elapsed < interval:
            await asyncio.sleep(interval - elapsed)
        self._last_request_at[account_id] = time.monotonic()

    async def _backoff(self, attempt: int) -> None:
        """指数退避（有上限，避免长时间挂死）。"""
        await asyncio.sleep(min(2.0**attempt, MAX_BACKOFF))

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    async def aclose(self) -> None:
        """关闭底层客户端（只关自己创建的，注入进来的由调用方负责）。"""
        for proxy_client in self._proxy_clients.values():
            await proxy_client.aclose()
        self._proxy_clients.clear()
        if self._pool_client is not None:
            await self._pool_client.aclose()
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> ChaoxingTransport:
        """支持 ``async with ChaoxingTransport(...) as transport:``。"""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """退出时关闭客户端。"""
        await self.aclose()
