"""AI 层契约：假 provider 确定性 + `build_provider` 的选择分支。

来源：计划 §4.3 —— `ai/` 是**唯一自制件**，`core` 只依赖 `LLMProvider` 协议。
这组用例保证"离线也能跑"这条路真的通：CI 里没有 key、演示时不想花钱，都靠它。
"""

from __future__ import annotations

from chaoxing.ai import FakeProvider, LLMProvider, build_provider
from chaoxing.client.model.quiz import Question, QuestionOption, QuestionType
from chaoxing.config import Settings


def multi_choice() -> Question:
    """造一道多选题。"""
    return Question(
        stem="下列属于危化品造成事故的主要特性是（）",
        qtype=QuestionType.MULTIPLE,
        options=[
            QuestionOption(label="A", text="易燃性"),
            QuestionOption(label="B", text="扩散性"),
        ],
    )


def settings(**ai: object) -> Settings:
    """造一份配置（不读仓库里那份 .env）。"""
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        secret_key="A" * 43 + "=",
        ai=dict(ai),
    )


async def test_fake_provider_is_deterministic() -> None:
    """同样的问题两次得到同样的答案（测试断言不会随机失败）。"""
    provider: LLMProvider = FakeProvider()
    question = multi_choice()

    first = await provider.answer(question)
    second = await provider.answer(question)

    assert first == second
    assert first.value == "AB"
    assert first.fingerprint == question.fingerprint


async def test_fake_provider_answers_by_question_type() -> None:
    """没有固定答案时按题型给形状合法的值（判断题 -> 对/错）。"""
    provider = FakeProvider()
    judge = Question(stem="这是判断题", qtype=QuestionType.JUDGE, options=[])

    assert (await provider.answer(judge)).value == "对"


def test_build_provider_selects_by_config() -> None:
    """`AI__PROVIDER=fake` 走假实现；没 key 时返回 None（引擎退化成只读缓存）。"""
    assert isinstance(build_provider(settings(provider="fake")), FakeProvider)
    assert build_provider(settings(provider="openai", api_key="")) is None
    assert build_provider(settings(provider="fake"), prompt="库里的提示词") is not None
