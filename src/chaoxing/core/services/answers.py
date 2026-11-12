"""答案引擎：**题库缓存优先**，未命中再问模型。

成本与延迟都在这里控制：同一道题（按题干指纹）只问一次模型，命中缓存直接复用；
每次命中把 ``hits`` 加一，所以"哪些题在反复出现"是看得见的（题库因此越长越值钱）。

模型调用被抽象成 :class:`chaoxing.ai.LLMProvider` 协议：测试里换假实现即可，
不需要 monkeypatch 任何第三方库。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from sqlalchemy import select

from chaoxing.ai.provider import LLMProvider
from chaoxing.client.model.quiz import Answer, Question, normalize_answer
from chaoxing.storage.models import AnswerCache
from chaoxing.storage.uow import UnitOfWork

if TYPE_CHECKING:  # 仅为类型：检索实现（httpx）不进本模块的运行时导入
    from chaoxing.ai.search import SearchProvider, Snippet
    from chaoxing.config import Settings

log = logging.getLogger(__name__)

#: 检索只带回少量片段（拼进提示词，不能喧宾夺主）
SEARCH_LIMIT = 3


class AnswerEngine:
    """答案引擎（缓存 + 模型）。

    :param uow: 工作单元
    :param provider: 模型提供者；为 ``None`` 时**只读缓存**（没配模型也不影响刷题）
    :param provider_factory: 需要作答时才按**当前**配置构造 provider —— 容器注入的就是它，
        所以提示词/模型改了不需要重启（计划 §5.10 的热更新语义）
    :param search: 网络检索实现；``None`` 时不做检索（检索是可选辅助功能）
    :param settings: 全局配置（读 ``ai.websearch_enabled``；``None`` 视为开启）
    """

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        provider: LLMProvider | None = None,
        provider_factory: Callable[[], Awaitable[LLMProvider | None]] | None = None,
        search: SearchProvider | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._uow = uow
        self._provider = provider
        self._provider_factory = provider_factory
        self._search = search
        self._settings = settings

    @property
    def has_provider(self) -> bool:
        """是否可能调模型（有 factory 时按"配置齐了"乐观判断）。"""
        return self._provider is not None or self._provider_factory is not None

    async def _resolve_provider(self) -> LLMProvider | None:
        """取当前 provider（有 factory 时按当前配置重建）。"""
        if self._provider_factory is None:
            return self._provider
        return await self._provider_factory()

    async def _gather_snippets(self, question: Question) -> list[Snippet]:
        """检索题干（检索是尽力而为：失败/超时只记日志，返回空）。

        :param question: 题目
        :returns: 片段列表；未开启检索或检索失败时为空
        """
        if self._search is None or (
            self._settings is not None and not self._settings.ai.websearch_enabled
        ):
            return []
        try:
            return await self._search.search(question.stem, limit=SEARCH_LIMIT)
        except Exception as exc:
            log.warning("检索失败（题干 %.20s…），降级为纯模型作答：%s", question.stem, exc)
            return []

    async def answer(self, question: Question) -> Answer:
        """给出一道题的答案（缓存优先）。

        :param question: 题目
        :returns: :class:`~chaoxing.client.model.quiz.Answer`；取不到时 ``value`` 为空
        """
        cached = await self._from_cache(question)
        if cached is not None:
            return cached
        provider = await self._resolve_provider()
        if provider is None:
            return Answer(fingerprint=question.fingerprint, source="none", reason="未配置模型")
        snippets = await self._gather_snippets(question)
        try:
            # 有片段才传：老实现（只认题目）不会被多出来的关键字参数打断
            if snippets:
                answer = await provider.answer(question, snippets=snippets)
            else:
                answer = await provider.answer(question)
        except Exception as exc:
            log.warning("模型作答失败（题干 %.20s…）：%s", question.stem, exc)
            return Answer(
                fingerprint=question.fingerprint, source="model", reason=f"调用失败：{exc}"
            )
        if answer.usable:
            await self._store(question, answer)
        return answer

    async def answer_many(self, questions: list[Question]) -> list[Answer]:
        """批量作答（顺序与入参一致）。

        :param questions: 题目列表
        :returns: 答案列表
        """
        answers: list[Answer] = []
        for question in questions:
            answers.append(await self.answer(question))
        return answers

    # ------------------------------------------------------------------ #
    # 缓存
    # ------------------------------------------------------------------ #
    async def _from_cache(self, question: Question) -> Answer | None:
        """查缓存（命中时把 ``hits`` 加一）。"""
        fingerprint = question.fingerprint
        async with self._uow.begin() as db:
            row = (
                await db.scalars(
                    select(AnswerCache).where(AnswerCache.question_hash == fingerprint)
                )
            ).first()
            if row is None:
                return None
            row.hits += 1
            value, source, confidence = row.answer, row.source, row.confidence
            await db.flush()
        log.debug("题库命中（hits=%d）", row.hits)
        return Answer(
            fingerprint=fingerprint,
            value=normalize_answer(value, qtype=question.qtype),
            source="cache" if source != "manual" else "manual",
            confidence=confidence,
            reason=f"题库命中（已用 {row.hits} 次）",
        )

    async def _store(self, question: Question, answer: Answer) -> None:
        """把新答案写进题库。"""
        async with self._uow.begin() as db:
            row = (
                await db.scalars(
                    select(AnswerCache).where(AnswerCache.question_hash == question.fingerprint)
                )
            ).first()
            if row is not None:
                row.answer = answer.value
                row.source = answer.source
                row.confidence = answer.confidence
            else:
                db.add(
                    AnswerCache(
                        question_hash=question.fingerprint,
                        question=question.stem,
                        answer=answer.value,
                        source=answer.source,
                        confidence=answer.confidence,
                        hits=0,
                    )
                )
            await db.flush()

    # ------------------------------------------------------------------ #
    # 题库维护
    # ------------------------------------------------------------------ #
    async def remember(self, question: Question, value: str, *, source: str = "manual") -> None:
        """人工录入 / 修正一条答案（来源标记为 manual，永不被模型覆盖）。

        :param question: 题目
        :param value: 答案（会按题型归一化）
        :param source: 来源标记
        """
        normalized = normalize_answer(value, qtype=question.qtype)
        if not normalized:
            return
        await self._store(
            question,
            Answer(
                fingerprint=question.fingerprint,
                value=normalized,
                source=source,
                confidence=1.0,
                reason="人工录入",
            ),
        )

    async def stats(self) -> dict[str, int]:
        """题库概览（设置页展示）。"""
        async with self._uow.read() as db:
            rows = list((await db.scalars(select(AnswerCache))).all())
        return {
            "total": len(rows),
            "manual": sum(1 for row in rows if row.source == "manual"),
            "model": sum(1 for row in rows if row.source != "manual"),
            "hits": sum(row.hits for row in rows),
        }

    async def list_cached(
        self, *, limit: int = 100, keyword: str = "", source: str = ""
    ) -> list[AnswerCache]:
        """列出题库（刷题页用）。

        :param limit: 返回条数（夹在 1~500）
        :param keyword: 题干关键词（空则不过滤）
        :param source: 只看某个来源（cache/model/manual）
        :returns: 题库条目（命中次数降序 —— 常考的排前面）
        """
        statement = (
            select(AnswerCache)
            .order_by(AnswerCache.hits.desc(), AnswerCache.id.desc())
            .limit(max(1, min(limit, 500)))
        )
        if keyword:
            statement = statement.where(AnswerCache.question.contains(keyword))
        if source:
            statement = statement.where(AnswerCache.source == source)

        async with self._uow.read() as db:
            return list((await db.scalars(statement)).all())

    async def forget(self, fingerprint: str) -> bool:
        """删掉一条缓存（下次遇到这道题会重新问模型）。

        :param fingerprint: 题干指纹
        :returns: 是否存在并被删除
        """
        async with self._uow.begin() as db:
            row = (
                await db.scalars(
                    select(AnswerCache).where(AnswerCache.question_hash == fingerprint)
                )
            ).first()
            if row is None:
                return False
            await db.delete(row)
            await db.flush()
            return True

    async def correct(self, fingerprint: str, value: str) -> AnswerCache | None:
        """人工修正一条答案（来源标 ``manual``，之后**永不被模型覆盖**）。

        :param fingerprint: 题干指纹
        :param value: 新答案
        :returns: 修正后的记录；不存在时 ``None``
        """
        async with self._uow.begin() as db:
            row = (
                await db.scalars(
                    select(AnswerCache).where(AnswerCache.question_hash == fingerprint)
                )
            ).first()
            if row is None:
                return None
            row.answer = value.strip()
            row.source = "manual"
            row.confidence = 1.0
            await db.flush()
            return row


__all__ = [
    "AnswerEngine",
]
