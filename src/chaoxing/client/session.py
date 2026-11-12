"""账号级会话状态：cookie、令牌、Referer。

为什么不能用 httpx 的 cookie jar
-------------------------------
一个 :class:`~chaoxing.client.transport.ChaoxingTransport` 要服务 N 个账号，
而 ``AsyncClient`` 的 cookie jar 是**客户端级共享**的 —— 两个账号会互相覆盖 cookie
（真实事故：A 账号的心跳带着 B 账号的 ``_uid`` 发出去，服务端判为越权）。

因此 transport 刻意不设 ``cookies``，所有 cookie 由本模块按账号持有，每请求显式传入。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from chaoxing.client.constants import COMMON_HEADERS


@dataclass(slots=True)
class SessionState:
    """单个超星账号的运行时状态。

    :param account_id: 账号 ID（registry 的 key）
    :param username: 手机号 / 用户名（仅用于展示与日志脱敏）
    :param cookies: 该账号的 cookie 集合
    :param tokens: 页面签发的令牌（``enc`` / ``dtoken`` / ``ktoken`` / ``stuenc`` …）
    :param referer: 最近一次访问的页面，作为后续请求的 Referer
    :param status: 会话状态（pending / logged_in / expired / captcha / error）
    :param last_used: 最近使用时间（monotonic 秒）
    :param dirty: 是否有尚未落库的变更（供节流持久化判断）
    """

    account_id: int
    username: str = ""
    cookies: dict[str, str] = field(default_factory=dict)
    tokens: dict[str, str] = field(default_factory=dict)
    referer: str = ""
    status: str = "pending"
    last_used: float = 0.0
    dirty: bool = False

    # ------------------------------------------------------------------ #
    # 请求侧
    # ------------------------------------------------------------------ #
    def cookie_header(self) -> str:
        """拼成 ``Cookie`` 请求头的值。"""
        return "; ".join(f"{key}={value}" for key, value in self.cookies.items())

    def headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """默认请求头 + 会话 cookie + Referer（账号级，不污染全局）。

        :param extra: 需要覆盖/追加的头
        :returns: 可直接交给 httpx 的 headers
        """
        headers = dict(COMMON_HEADERS)
        # `thirdRegist=0` 是浏览器侧登录后必带的标记（真机实测提交测验时它会随请求发出）。
        # 在这里补而不是放进 dataclass 默认值：会话"有没有 cookie"决定它算不算已登录，
        # 不能被一个常量污染（`test_authenticated_requires_status_and_cookies` 钉过这个语义）。
        self.cookies.setdefault("thirdRegist", "0")
        if self.cookies:
            headers["Cookie"] = self.cookie_header()
        if self.referer:
            headers["Referer"] = self.referer
        if extra:
            headers.update(extra)
        return headers

    def touch(self) -> None:
        """记录"刚刚用过这个会话"。"""
        self.last_used = time.monotonic()

    # ------------------------------------------------------------------ #
    # 响应侧
    # ------------------------------------------------------------------ #
    def absorb(self, response: httpx.Response) -> int:
        """吸收一次响应的 ``Set-Cookie``。

        **必须连 302 跳转链一起吸收**：``follow_redirects=True`` 时，
        最终响应的 ``.cookies`` 只包含最后一步的 Set-Cookie，
        而登录链路里最关键的会话 cookie 恰恰在中间那几次 302 上。
        踩过的坑：漏掉 history 里的 cookie，表现为"登录成功但下一个请求就 401"。

        :param response: httpx 响应（含 history）
        :returns: 本次吸收/更新的 cookie 条数
        """
        changed = 0
        for item in [*response.history, response]:
            for name, value in item.cookies.items():
                if value == "":
                    if self.cookies.pop(name, None) is not None:
                        changed += 1
                    continue
                if self.cookies.get(name) != value:
                    self.cookies[name] = value
                    changed += 1
        if changed:
            self.dirty = True
            self.referer = str(response.url)
        return changed

    # ------------------------------------------------------------------ #
    # 令牌
    # ------------------------------------------------------------------ #
    def update_tokens(self, **values: str) -> None:
        """更新页面签发的令牌（空值会被忽略，避免用空串覆盖有效令牌）。"""
        changed = False
        for name, value in values.items():
            if value and self.tokens.get(name) != value:
                self.tokens[name] = value
                changed = True
        if changed:
            self.dirty = True

    def token(self, name: str, default: str = "") -> str:
        """取令牌。

        :param name: 令牌名（``enc`` / ``dtoken`` / ``ktoken`` …）
        :param default: 缺省值
        :returns: 令牌值
        """
        return self.tokens.get(name, default)

    # ------------------------------------------------------------------ #
    # 持久化
    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict[str, Any]:
        """可序列化的快照（P2 起由 storage 落库，当前由 CLI 写本地文件）。"""
        return {
            "account_id": self.account_id,
            "username": self.username,
            "cookies": dict(self.cookies),
            "tokens": dict(self.tokens),
            "referer": self.referer,
            "status": self.status,
        }

    @classmethod
    def from_snapshot(cls, raw: dict[str, Any]) -> SessionState:
        """由快照恢复（``last_used`` 与 ``dirty`` 不持久化）。"""
        return cls(
            account_id=int(raw.get("account_id", 0)),
            username=str(raw.get("username", "")),
            cookies={str(k): str(v) for k, v in dict(raw.get("cookies") or {}).items()},
            tokens={str(k): str(v) for k, v in dict(raw.get("tokens") or {}).items()},
            referer=str(raw.get("referer", "")),
            status=str(raw.get("status", "pending")),
        )

    @property
    def is_authenticated(self) -> bool:
        """是否具备"已登录"的最小特征（有 ``_uid`` 之类的会话 cookie）。"""
        return self.status == "logged_in" and bool(self.cookies)
