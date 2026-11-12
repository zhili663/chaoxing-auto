"""按配置构造 provider（`AI__PROVIDER=openai|fake`；没配 api_key 返回 `None`）。"""

from __future__ import annotations

from chaoxing.ai.prompts import DEFAULT_SYSTEM_PROMPT
from chaoxing.ai.provider import LLMProvider
from chaoxing.ai.providers.fake import FakeProvider
from chaoxing.ai.providers.openai_sdk import OpenAIProvider


def build_provider(settings: object, *, prompt: str | None = None) -> LLMProvider | None:
    """按配置构造模型提供者（没配 api_key 就返回 ``None``）。

    :param settings: 全局配置
    :param prompt: 系统提示词（**由调用方从 `prompts` 表取**，见 `PromptService`）；
        留空用内置默认 —— 提示词不再有"藏在配置里的第三份来源"
    :returns: :class:`OpenAIProvider` / :class:`FakeProvider` / ``None``
    """
    ai = getattr(settings, "ai", None)
    if str(getattr(ai, "provider", "") or "").strip().lower() == "fake":
        return FakeProvider()
    if ai is None or not getattr(ai, "api_key", ""):
        return None
    return OpenAIProvider(
        base_url=getattr(ai, "base_url", ""),
        api_key=ai.api_key,
        model=getattr(ai, "model", "gpt-4o-mini"),
        system_prompt=prompt or DEFAULT_SYSTEM_PROMPT,
        timeout=float(getattr(ai, "timeout", 60.0)),
    )
