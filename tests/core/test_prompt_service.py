"""提示词入库：服务层 + 与答案引擎的接线。

来源：计划 §5.10 —— 提示词必须入库、改完即生效。此前 `prompts` 表**建了但没有读取方**
（表是死代码，提示词只能改代码常量），这组用例把"引擎真的会用库里的文本"钉住。
"""

from __future__ import annotations

from chaoxing.ai import DEFAULT_SYSTEM_PROMPT, build_provider
from chaoxing.config import Settings
from chaoxing.core.services.prompts import PROMPT_KEY_SYSTEM, PromptService
from chaoxing.storage.uow import UnitOfWork


def ai_settings(**overrides: object) -> Settings:
    """造一份带 API key 的配置（不读仓库里那份 .env）。"""
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        secret_key="A" * 43 + "=",
        ai={"api_key": "sk-test", **overrides},
    )


async def test_upsert_creates_then_bumps_version(uow: UnitOfWork) -> None:
    """首次写入 version=1，再次写入自增（便于回答"这轮答案是哪版提示词"）。"""
    service = PromptService(uow=uow)

    first = await service.upsert(PROMPT_KEY_SYSTEM, "第一版", user_id=None)
    assert first.version == 1

    second = await service.upsert(PROMPT_KEY_SYSTEM, "第二版", user_id=None)
    assert second.version == 2
    assert await service.get(PROMPT_KEY_SYSTEM) == "第二版"


async def test_get_returns_none_when_absent(uow: UnitOfWork) -> None:
    """没写过的 key 返回 None（调用方据此回落内置默认）。"""
    assert await PromptService(uow=uow).get(PROMPT_KEY_SYSTEM) is None


async def test_list_all_is_ordered_and_complete(uow: UnitOfWork) -> None:
    """列表按 key 排序，内容齐全。"""
    service = PromptService(uow=uow)
    await service.upsert("b.key", "B", user_id=None)
    await service.upsert("a.key", "A", user_id=None)

    rows = await service.list_all()

    assert [row.key for row in rows] == ["a.key", "b.key"]


def test_provider_uses_prompt_override() -> None:
    """`build_provider(prompt=...)` 用传入文本；不传时回落内置默认。"""
    settings = ai_settings()

    assert build_provider(settings).system_prompt == DEFAULT_SYSTEM_PROMPT
    assert build_provider(settings, prompt="库里的提示词").system_prompt == "库里的提示词"


def test_provider_absent_without_api_key() -> None:
    """没配密钥就没有 provider（引擎退化成"只读题库缓存"）。"""
    assert build_provider(ai_settings(api_key="")) is None
