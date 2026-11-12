"""日志读取：JSON 行的尾部窗口 + 级别过滤。

放在 ``obs`` 而不是路由里：解析格式是**日志基建**的属性（structlog 的 JSON 行），
换格式时只该改这一处；路由只负责鉴权与参数裁剪。

尾部按字节窗口读（默认 256 KB），不把整个文件载入内存。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: 级别 → 保留哪些级别（`min_level` 语义）
LEVELS: dict[str, set[str]] = {
    "DEBUG": {"debug", "info", "warning", "error", "critical"},
    "INFO": {"info", "warning", "error", "critical"},
    "WARNING": {"warning", "error", "critical"},
    "ERROR": {"error", "critical"},
}

#: 尾部窗口：只读文件最后这么多字节
TAIL_BYTES = 256 * 1024


def read_tail(path: Path, lines: int) -> list[str]:
    """读文件最后 ``lines`` 行。

    :param path: 文件路径
    :param lines: 行数
    :returns: 文本行（文件不存在时为空表）
    """
    if not path.is_file():
        return []
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - TAIL_BYTES))
        raw = handle.read().decode("utf-8", errors="replace")
    return raw.splitlines()[-max(1, lines) :]


def read_recent(path: Path, *, lines: int = 200, min_level: str = "INFO") -> list[dict[str, Any]]:
    """读日志尾部并按级别过滤。

    :param path: 日志文件
    :param lines: 最多返回多少条（先取尾部再过滤）
    :param min_level: 最低级别（不认识的级别按 INFO 处理）
    :returns: 形如 ``{"level", "logger", "event", "timestamp"}`` 的记录
    """
    wanted = LEVELS.get(min_level.upper(), LEVELS["INFO"])
    entries: list[dict[str, Any]] = []
    for raw in read_tail(path, lines):
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue  # 非 JSON 行（手工追加、示例文本）直接跳过
        if str(item.get("level", "")).lower() not in wanted:
            continue
        entries.append(
            {
                "level": item.get("level"),
                "logger": item.get("logger"),
                "event": item.get("event"),
                "timestamp": item.get("timestamp"),
            }
        )
    return entries


__all__ = ["LEVELS", "TAIL_BYTES", "read_recent", "read_tail"]
