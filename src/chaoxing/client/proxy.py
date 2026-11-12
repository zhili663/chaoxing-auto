"""代理池：轮换出口 IP，规避账号级风控。

为什么需要（实测 2026-10-15）：连续数小时满负荷跑批后，学习通对账号的
``mooc-ans/*`` 接口开始回 403（换票被拦，响应体是通用错误页）。**换出口 IP 是最直接的解法** ——
同一份请求、同一个账号，换个出口就恢复。

设计取舍（都来自真实事故）：

* **同账号粘性**：账号突然换 IP 本身是风控信号，所以**默认不主动轮换**；
  只有该出口**被拒**（403/风控页）才切到下一个（:meth:`ProxyPool.mark_blocked`）。
* **不重放失败请求**：切换只影响后续请求 —— 不做"失败即换 IP 重试"，
  那正是 2026-10-14 那次请求风暴的形态。
* **池子为空时零影响**：没有任何代理就一切照旧直连，不改变现有行为。

导入格式（一行一条，见 :func:`parse_line`）::

    1.2.3.4:8080                       # 无认证
    1.2.3.4:8080:user:pass             # 冒号四段
    user:pass@1.2.3.4:8080             # @ 简写
    http://user:pass@1.2.3.4:8080      # 完整 URL
    socks5://1.2.3.4:1080              # 带协议
"""

from __future__ import annotations

import contextlib
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

#: ``user:pass@host:port`` 或 ``host:port``（可带协议前缀）
_URL_LIKE = re.compile(
    r"^(?P<scheme>[a-z0-9+.\-]+)://(?:(?P<user>[^:@/]+):(?P<pass>[^@/]+)@)?(?P<host>[^:@/]+):(?P<port>\d+)$",
    re.IGNORECASE,
)
#: ``user:pass@host:port``（不带协议前缀的简写）
_AT_PART = re.compile(r"^(?P<user>[^:@/]+):(?P<pass>[^@/]+)@(?P<host>[^:@/]+):(?P<port>\d+)$")
#: ``host:port:user:pass``（部分供应商给的就是这四段）
_FOUR_PART = re.compile(r"^(?P<host>[^:]+):(?P<port>\d+):(?P<user>[^:]+):(?P<pass>.+)$")
#: ``host:port``
_TWO_PART = re.compile(r"^(?P<host>[^:]+):(?P<port>\d+)$")


@dataclass(slots=True)
class ProxyEntry:
    """一个出口。``url`` 是 httpx 认的完整形式；``label`` 是日志里用的短标签（**不含口令**）。"""

    url: str
    label: str
    scheme: str = "http"
    status: str = "ok"  # ok / cooling
    fail_count: int = 0
    ok_count: int = 0
    last_used: float = 0.0
    cooldown_until: float = 0.0
    latency_ms: float = 0.0

    @property
    def cooling(self) -> bool:
        """是否还在冷却期（冷却到点自动恢复 ok）。"""
        return self.status == "cooling" and time.monotonic() < self.cooldown_until


def parse_line(line: str) -> ProxyEntry | None:
    """把一行导入文本解析成 :class:`ProxyEntry`；无法识别返回 ``None``。

    :param line: 一行（可带注释：``#`` 之后忽略；空行忽略）
    """
    text = (line or "").split("#", 1)[0].strip()
    if not text:
        return None

    match = _URL_LIKE.match(text)
    if match:
        groups = match.groupdict()
        scheme = groups["scheme"].lower()
        user, password = groups.get("user"), groups.get("pass")
        auth = f"{user}:{password}@" if user is not None else ""
        host, port = groups["host"], groups["port"]
    else:
        match = _AT_PART.match(text)
        if match:
            groups = match.groupdict()
            scheme = "http"
            host, port = groups["host"], groups["port"]
            auth = f"{groups['user']}:{groups['pass']}@"
        else:
            match = _FOUR_PART.match(text)
            if match:
                groups = match.groupdict()
                scheme = "http"
                host, port = groups["host"], groups["port"]
                auth = f"{groups['user']}:{groups['pass']}@"
            else:
                match = _TWO_PART.match(text)
                if not match:
                    return None
                groups = match.groupdict()
                scheme = "http"
                host, port = groups["host"], groups["port"]
                auth = ""

    url = f"{scheme}://{auth}{host}:{port}"
    return ProxyEntry(url=url, label=f"{host}:{port}", scheme=scheme)


@dataclass
class ProxyPool:
    """代理池：按 key（通常是账号）粘性分配出口，被拒时切换。

    :param entries: 出口列表
    :param cooldown_s: 某个出口被拒后的冷却秒数（默认 5 分钟）
    """

    entries: list[ProxyEntry] = field(default_factory=list)
    cooldown_s: float = 300.0
    #: key -> 出口（粘性绑定；key 一般是账号 ID）
    _bound: dict[str, ProxyEntry] = field(default_factory=dict, repr=False)
    #: 轮询游标（给新 key 挑"最久没用过"的出口）
    _cursor: int = 0

    # ------------------------------------------------------------------ #
    # 构造
    # ------------------------------------------------------------------ #
    @classmethod
    def from_text(cls, text: str, *, cooldown_s: float = 300.0) -> ProxyPool:
        """从多行文本构造（一行一条，重复的自动去重、保持顺序）。"""
        pool = cls(cooldown_s=cooldown_s)
        seen: set[str] = set()
        for raw in (text or "").splitlines():
            entry = parse_line(raw)
            if entry is None or entry.url in seen:
                continue
            seen.add(entry.url)
            pool.entries.append(entry)
        return pool

    @classmethod
    def from_file(cls, path: str | Path, *, cooldown_s: float = 300.0) -> ProxyPool:
        """从池文件构造（文件不存在 = 空池 = 全直连；读取失败也退回空池，不让跑批起不来）。"""
        target = Path(path)
        text = ""
        with contextlib.suppress(OSError):
            if target.exists():
                text = target.read_text(encoding="utf-8")
        return cls.from_text(text, cooldown_s=cooldown_s)

    @property
    def enabled(self) -> bool:
        """池子里有没有可用出口（没有就全走直连）。"""
        return bool(self.entries)

    def sync_remote(self, entries: Sequence[ProxyEntry]) -> None:
        """用远端快照替换出口列表，但**保留同 URL 的粘性与冷却状态**。

        直接 ``self.entries = entries`` 会丢掉两类状态，后果都很实际：

        * 同账号已经绑定的出口突然变成另一个对象 → 账号换 IP，触发风控；
        * 正在冷却的坏出口被"洗白" → 下一请求又撞同一个 403。

        远端已消失的出口会解绑；仍存在的出口保留 ``status``/``fail_count``/
        ``ok_count``/``cooldown_until``，只更新列表本身。

        :param entries: 远端返回的出口（需已去重）
        """
        incoming = {item.url: item for item in entries}
        previous = {item.url: item for item in self.entries}

        for url, fresh in incoming.items():
            old = previous.get(url)
            if old is None:
                continue
            fresh.status = old.status
            fresh.fail_count = old.fail_count
            fresh.ok_count = old.ok_count
            fresh.last_used = old.last_used
            fresh.cooldown_until = old.cooldown_until
            fresh.latency_ms = old.latency_ms

        # 绑定必须指向新对象；远端已不存在的出口直接解绑。
        for key, bound in list(self._bound.items()):
            replacement = incoming.get(bound.url)
            if replacement is None:
                del self._bound[key]
            else:
                self._bound[key] = replacement

        self.entries = list(incoming.values())

    # ------------------------------------------------------------------ #
    # 挑选与反馈
    # ------------------------------------------------------------------ #
    def _available(self) -> list[ProxyEntry]:
        return [item for item in self.entries if not item.cooling]

    def pick(self, key: str) -> ProxyEntry | None:
        """按 key 取出出口：**粘性优先**；原出口被冷却就换一个。

        :param key: 绑定键（一般传账号 ID 的字符串）
        :returns: 出口；池空或全部冷却时返回 ``None``（调用方直连）
        """
        if not self.entries:
            return None

        current = self._bound.get(key)
        if current is not None and not current.cooling:
            current.last_used = time.monotonic()
            return current

        candidates = self._available()
        if not candidates:
            # 全在冷却 —— 直连兜底，别把整个跑批卡死
            return None

        # 挑"最久没用过"的：天然把负载摊平，且不引入随机性（便于复现）
        entry = min(candidates, key=lambda item: item.last_used)
        entry.last_used = time.monotonic()
        self._bound[key] = entry
        return entry

    def rotate(self, key: str) -> ProxyEntry | None:
        """主动解绑并切到下一个可用出口（**不是**等 403 才换）。

        只把旧出口的 ``last_used`` 推到当前时间，再由 :meth:`pick` 选最久未用的
        出口；这样单出口池不会切到别处，多出口池会轮换。旧出口不冷却 ——
        主动轮换不是失败，不应把它标记为坏。

        :param key: 账号键
        :returns: 新出口；池空/全部冷却时 ``None``
        """
        previous = self._bound.pop(key, None)
        if previous is not None:
            previous.last_used = time.monotonic()
        return self.pick(key)

    def mark_blocked(self, entry: ProxyEntry, *, reason: str = "") -> None:
        """把出口标记为被拒：进入冷却，并解绑所有键（下次会挑新出口）。"""
        entry.status = "cooling"
        entry.fail_count += 1
        entry.cooldown_until = time.monotonic() + self.cooldown_s
        for key, bound in list(self._bound.items()):
            if bound is entry:
                del self._bound[key]

    def mark_ok(self, entry: ProxyEntry, *, latency_ms: float = 0.0) -> None:
        """记录一次成功（用于统计与恢复）。"""
        entry.status = "ok"
        entry.ok_count += 1
        entry.cooldown_until = 0.0
        if latency_ms:
            entry.latency_ms = latency_ms

    # ------------------------------------------------------------------ #
    # 观测
    # ------------------------------------------------------------------ #
    def stats(self) -> list[dict[str, object]]:
        """给 CLI/UI 用的概览（**不含口令** —— 只暴露 label）。"""
        now = time.monotonic()
        return [
            {
                "label": item.label,
                "status": "cooling" if item.cooling else "ok",
                "ok": item.ok_count,
                "failed": item.fail_count,
                "idle_s": round(now - item.last_used, 1) if item.last_used else None,
                "cooldown_left_s": round(item.cooldown_until - now, 1) if item.cooling else 0.0,
            }
            for item in self.entries
        ]
