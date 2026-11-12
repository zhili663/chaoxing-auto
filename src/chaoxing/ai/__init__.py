"""AI 层：模型调用的唯一出口。

只有这一层认识 `openai` / `instructor`：外面（`core`）只认 :class:`LLMProvider` 协议。
SDK 相关的东西**按需导入**（见下面的模块级 ``__getattr__``），
这样 `import chaoxing.ai` 不会把第三方 SDK 一起拖进来 —— `.importlinter` 里有契约钉住。
"""

from __future__ import annotations

import importlib
from typing import Any

from chaoxing.ai.prompts import DEFAULT_SYSTEM_PROMPT, render_question
from chaoxing.ai.provider import LLMProvider

__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "DuckDuckGoSearch",
    "FakeProvider",
    "FakeSearch",
    "LLMProvider",
    "OpenAIProvider",
    "SearchProvider",
    "Snippet",
    "build_provider",
    "render_question",
]

_LAZY: dict[str, tuple[str, str]] = {
    "DuckDuckGoSearch": ("chaoxing.ai.search", "DuckDuckGoSearch"),
    "FakeProvider": ("chaoxing.ai.providers.fake", "FakeProvider"),
    "FakeSearch": ("chaoxing.ai.search", "FakeSearch"),
    "OpenAIProvider": ("chaoxing.ai.providers.openai_sdk", "OpenAIProvider"),
    "SearchProvider": ("chaoxing.ai.search", "SearchProvider"),
    "Snippet": ("chaoxing.ai.search", "Snippet"),
    "build_provider": ("chaoxing.ai.factory", "build_provider"),
}


def __getattr__(name: str) -> Any:
    """惰性导出带 SDK 的实现（首次访问才导入）。

    :param name: 属性名
    :returns: 对应对象
    :raises AttributeError: 名字不存在
    """
    if name in _LAZY:
        module_name, attribute = _LAZY[name]
        value = getattr(importlib.import_module(module_name), attribute)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
