"""按账号隔离的结构化日志。

为什么单独一份而不是直接写 ``app.log``：刷课失败（尤其测验）需要带上**服务端原始返回**、
异常类型与 traceback；把这些全塞进主日志会迅速淹没进程/请求级日志。这里按
``data/logs/accounts/<account_id>.jsonl`` 落盘，主日志只留一行摘要。

实现约束：

* **同步追加**：日志量小且本地盘；不引入后台队列，进程退出也不丢；
* **轮转**：单文件超过 ``max_bytes`` 后滚到 ``.1``，避免长跑把盘写满；
* **脱敏**：cookie / token / password / authorization 等字段递归替换；
* **失败不致命**：磁盘错误只记一条 stderr，不影响刷课主流程。
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: 这些键（不区分大小写）一律脱敏
_SENSITIVE = (
    "cookie",
    "set-cookie",
    "authorization",
    "password",
    "passwd",
    "secret",
    "token",
    "session",
    "csrf",
)


def _redact(value: Any) -> Any:
    """递归脱敏（字典键命中 ``_SENSITIVE`` 时保留键、值替换）。"""
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            text = str(key)
            if any(marker in text.lower() for marker in _SENSITIVE):
                cleaned[text] = "***"
            else:
                cleaned[text] = _redact(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


class AccountLogWriter:
    """按账号写 JSONL 日志。

    :param directory: 输出目录（通常是 ``settings.log_dir / "accounts"``）
    :param max_bytes: 单文件轮转阈值
    :param max_line_chars: 单行上限（防止异常 payload 把日志写爆）
    """

    def __init__(
        self,
        directory: Path,
        *,
        max_bytes: int = 5 * 1024 * 1024,
        max_line_chars: int = 20_000,
    ) -> None:
        self.directory = Path(directory)
        self.max_bytes = max_bytes
        self.max_line_chars = max_line_chars
        self._lock = threading.Lock()

    def path_for(self, account_id: int) -> Path:
        """账号日志文件路径。"""
        return self.directory / f"{account_id}.jsonl"

    def write(self, account_id: int, event: str, **fields: Any) -> None:
        """追加一条账号日志（失败只记 stderr，不打断业务）。

        :param account_id: 账号 ID
        :param event: 事件名（``task_failed`` / ``task_finished`` …）
        :param fields: 结构化字段；敏感键自动脱敏
        """
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "account_id": account_id,
            "event": event,
            **_redact(fields),
        }
        line = json.dumps(payload, ensure_ascii=False, default=str)
        if len(line) > self.max_line_chars:
            line = line[: self.max_line_chars] + "…<truncated>"
        try:
            with self._lock:
                self.directory.mkdir(parents=True, exist_ok=True)
                path = self.path_for(account_id)
                if path.exists() and path.stat().st_size >= self.max_bytes:
                    path.replace(self.directory / f"{account_id}.1.jsonl")
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except OSError as exc:
            # 日志落盘失败不能影响刷课；只保留一条短警告（不写主日志文件）
            sys.stderr.write(f"[account-log] {account_id}: {exc}\n")

    def tail(self, account_id: int, limit: int = 100) -> list[str]:
        """读取最近 ``limit`` 行（跨当前文件与 ``.1`` 轮转文件）。

        :param account_id: 账号 ID
        :param limit: 最大行数（从新到旧读取后按时间正序返回）
        :returns: 原始 JSONL 行
        """
        target = max(1, min(int(limit), 2000))
        lines: list[str] = []
        for path in (self.directory / f"{account_id}.1.jsonl", self.path_for(account_id)):
            if not path.exists():
                continue
            try:
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    lines.extend(handle.read().splitlines())
            except OSError:
                continue
        return lines[-target:]
