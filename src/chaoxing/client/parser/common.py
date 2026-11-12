"""解析层的公共小工具（属性取值、数字解析、JSON 抠取）。"""

from __future__ import annotations

import re
from typing import Any, Protocol

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


class _AttrNode(Protocol):
    """只要求 bs4 节点的 ``get`` 能力（便于测试里塞替身）。"""

    def get(self, key: str, default: Any = None) -> Any: ...


def attr(node: _AttrNode, *names: str, default: str = "") -> str:
    """按候选名从 HTML 节点取属性值。

    HTML 的属性名大小写不敏感，不同解析器给出的大小写也不一致，
    因此逐个试（``courseId`` / ``courseid`` / ``COURSEID``）。

    :param node: bs4 节点（或其替身）
    :param names: 候选属性名，按优先级排列
    :param default: 都没取到时的返回值
    :returns: 去除首尾空白的字符串
    """
    for name in names:
        value = node.get(name)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return default


def to_float(value: Any) -> float:
    """稳健的浮点解析。

    ``"12:34"`` → 754（mm:ss，legacy 的视频时长格式）、``"90"`` → 90、
    ``589`` → 589.0；无法解析时返回 0.0（宁可判 0 也不要抛异常打断整条链路）。

    :param value: 任意值
    :returns: 解析出的浮点数
    """
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    if ":" in text:  # mm:ss 或 hh:mm:ss
        total = 0.0
        for part in (piece for piece in text.split(":") if piece.strip()):
            match = _NUM_RE.search(part)
            total = total * 60 + (float(match.group()) if match else 0.0)
        return total
    match = _NUM_RE.search(text)
    return float(match.group()) if match else 0.0


def extract_json_object(text: str, anchor: str) -> str | None:
    """按大括号配对从文本里抠出一段完整 JSON 对象。

    比正则可靠：能正确跳过字符串内部的括号（学习通的 otherInfo 里就带 ``{}``）。

    :param text: 页面源码
    :param anchor: 起点标记（例如 ``'{"attachments"'``）
    :returns: JSON 文本；找不到或括号不闭合时返回 ``None``
    """
    start = text.find(anchor)
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None
