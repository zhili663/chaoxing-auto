"""答案引擎：缓存优先、模型兜底、失败不外溢。

来源：成本与稳定性两个反复出现的诉求 ——
① 同一道题反复出现时不能每次都问模型（题库因此越长越值钱）；
② 模型挂了不能让整轮刷课跟着挂（其它任务点还要继续刷）。
"""

from __future__ import annotations

from sqlalchemy import select

from chaoxing.client.model.quiz import (
    Answer,
    Question,
    QuestionOption,
    QuestionType,
    normalize_answer,
)
from chaoxing.core.services.answers import AnswerEngine
from chaoxing.storage.models import AnswerCache
from chaoxing.storage.uow import UnitOfWork


class FakeProvider:
    """答案提供者替身（记录调用次数，可制造失败）。"""

    def __init__(self, value: str = "AB", *, confidence: float = 0.9, boom: bool = False) -> None:
        self.value = value
        self.confidence = confidence
        self.boom = boom
        self.calls = 0

    async def answer(self, question: Question) -> Answer:
        self.calls += 1
        if self.boom:
            raise RuntimeError("模型服务不可用")
        return Answer(
            fingerprint=question.fingerprint,
            value=self.value,
            source="model",
            confidence=self.confidence,
        )


def multi_choice(stem: str = "下列属于危化品造成事故的主要特性是（）") -> Question:
    """造一道多选题。"""
    return Question(
        stem=stem,
        qtype=QuestionType.MULTIPLE,
        options=[
            QuestionOption(label="A", text="易燃性"),
            QuestionOption(label="B", text="扩散性"),
            QuestionOption(label="C", text="毒害性"),
        ],
    )


def test_normalize_answer_variants() -> None:
    """各题型的答案格式不同，归一化就一次说清。"""
    assert normalize_answer("a", qtype=QuestionType.SINGLE) == "A"
    assert normalize_answer("ba", qtype=QuestionType.MULTIPLE) == "AB"
    assert normalize_answer("C A B", qtype=QuestionType.MULTIPLE) == "ABC"
    assert normalize_answer("正确", qtype=QuestionType.JUDGE) == "对"
    assert normalize_answer("false", qtype=QuestionType.JUDGE) == "错"
    assert normalize_answer("  因为 所以  ", qtype=QuestionType.SHORT) == "因为 所以"
    assert normalize_answer("", qtype=QuestionType.SINGLE) == ""


async def test_without_provider_only_cache_is_used(uow: UnitOfWork) -> None:
    """没配模型时不能崩：返回空答案，让上层跳过这道题。"""
    engine = AnswerEngine(uow=uow)

    answer = await engine.answer(multi_choice())

    assert answer.usable is False
    assert answer.source == "none"


async def test_provider_answer_is_persisted(uow: UnitOfWork) -> None:
    """模型答出来后要写进题库（下次直接命中）。"""
    provider = FakeProvider(value="AB", confidence=0.8)
    engine = AnswerEngine(uow=uow, provider=provider)

    first = await engine.answer(multi_choice())

    assert first.value == "AB"
    assert provider.calls == 1
    async with uow.read() as session:
        stored = (await session.scalars(select(AnswerCache))).one()
        assert stored.answer == "AB"
        assert stored.confidence == 0.8


async def test_second_identical_question_hits_cache(uow: UnitOfWork) -> None:
    """同一道题第二次不再问模型（题库的价值就在这里）。"""
    provider = FakeProvider(value="AB")
    engine = AnswerEngine(uow=uow, provider=provider)

    await engine.answer(multi_choice())
    second = await engine.answer(multi_choice())

    assert provider.calls == 1, "第二次又问模型了，缓存没命中"
    assert second.source == "cache"
    async with uow.read() as session:
        stored = (await session.scalars(select(AnswerCache))).one()
        assert stored.hits == 1


async def test_cache_hit_ignores_punctuation_differences(uow: UnitOfWork) -> None:
    """题干标点/空白不同也要命中同一道题（否则缓存形同虚设）。"""
    provider = FakeProvider(value="A")
    engine = AnswerEngine(uow=uow, provider=provider)

    await engine.answer(multi_choice("下列属于危化品造成事故的主要特性是（）"))
    await engine.answer(multi_choice("下列属于危化品造成事故的主要特性是( )"))

    assert provider.calls == 1


async def test_provider_failure_is_swallowed(uow: UnitOfWork) -> None:
    """模型挂了只是这道题没答案，不能让整轮刷课崩掉。"""
    engine = AnswerEngine(uow=uow, provider=FakeProvider(boom=True))

    answer = await engine.answer(multi_choice())

    assert answer.usable is False
    assert "调用失败" in answer.reason


async def test_manual_answer_wins_over_model(uow: UnitOfWork) -> None:
    """人工录入的答案优先（source 保持 manual，不被模型覆盖）。"""
    engine = AnswerEngine(uow=uow, provider=FakeProvider(value="AB"))

    await engine.remember(multi_choice(), "C")
    answer = await engine.answer(multi_choice())

    assert answer.value == "C"
    assert answer.source == "manual"


async def test_stats_reports_library_size(uow: UnitOfWork) -> None:
    """题库概览（设置页展示）。"""
    engine = AnswerEngine(uow=uow, provider=FakeProvider())
    await engine.answer(multi_choice())
    await engine.remember(multi_choice("另一道题？"), "A")

    stats = await engine.stats()

    assert stats["total"] == 2
    assert stats["manual"] == 1
    assert stats["model"] == 1
