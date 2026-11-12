"""确定性假 provider：离线演示与测试用（`AI__PROVIDER=fake` 时启用）。

同一个问题永远给同一个答案 —— 断言不会随机失败。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from chaoxing.client.model.quiz import Answer, Question, QuestionType

if TYPE_CHECKING:  # 仅为类型：假实现不碰检索，也不该拖入 httpx
    from chaoxing.ai.search import Snippet


class FakeProvider:
    """按题型给一个**形状合法**的稳定答案（不是为了答对，是为了流程跑得通）。"""

    def __init__(self, *, answer: str = "", confidence: float = 0.5) -> None:
        """初始化。

        :param answer: 固定答案；留空则按题型推一个
        :param confidence: 固定置信度
        """
        self._answer = answer
        self._confidence = confidence

    async def answer(
        self, question: Question, *, snippets: Sequence[Snippet] | None = None
    ) -> Answer:
        """给出一道题的答案。

        :param question: 题目
        :param snippets: 检索片段（假实现忽略 —— 它只保证形状合法）
        :returns: 答案记录
        """
        return Answer(
            fingerprint=question.fingerprint,
            value=self._answer or _by_question_type(question),
            source="model",
            confidence=self._confidence,
            reason="fake provider（离线）",
        )


def _by_question_type(question: Question) -> str:
    """按题型给个形状合法的答案。

    :param question: 题目
    :returns: 答案文本
    """
    if question.qtype is QuestionType.MULTIPLE:
        return "AB"
    if question.qtype is QuestionType.JUDGE:
        return "对"
    if question.qtype is QuestionType.MATCHING:
        # 形状合法即可（每一对给个不同字母）；连线题要求「序号=字母」逐行
        return "\n".join(
            f"{name}={chr(ord('A') + index % 26)}" for index, (name, _) in enumerate(question.pairs)
        )
    if question.qtype is QuestionType.FILL or question.qtype is QuestionType.SHORT:
        return question.stem[:20] or "略"
    return "A"
