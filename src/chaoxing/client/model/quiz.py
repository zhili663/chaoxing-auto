"""测验题目与答案模型。

答题链路上有三类角色，各自职责清晰：

* :class:`Question` —— 从页面解析出来的**题目**（题干、选项、题型）；
* :class:`Answer` —— 答案引擎给出的**答案**（值、来源、置信度）；
* ``Answer.value`` 的格式与题意强相关（单选 ``"A"``、多选 ``"AB"``、判断 ``"对"``），
  因此在给出答案时就把它**归一化**成学习通能接受的形态，提交层不再做二次判断。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from chaoxing.client.model.base import ChaoxingModel
from chaoxing.utils.text import question_hash, squish


class QuestionType(StrEnum):
    """题型。"""

    SINGLE = "single"
    MULTIPLE = "multiple"
    JUDGE = "judge"
    MATCHING = "matching"
    FILL = "fill"
    SHORT = "short"
    UNKNOWN = "unknown"


class QuestionOption(ChaoxingModel):
    """一个选项。"""

    label: str = ""
    text: str = ""


class Question(ChaoxingModel):
    """一道题。

    :cvar index: 题目在页面里的顺序（提交时按它对应 ``answer{questionId}``）
    :cvar question_id: 题目 ID（页面可能没有，缺失时由 :attr:`fingerprint` 兜底）
    :cvar pairs: 连线题的**左项**（``(1-based 序号, 文本)``）；非连线题为空。
        连线题的 :attr:`options` 放的是**右项**（字母 + 文本），因此模型两边都看得到。
    """

    index: int = 0
    question_id: str = ""
    stem: str = ""
    options: list[QuestionOption] = Field(default_factory=list)
    pairs: list[tuple[str, str]] = Field(default_factory=list)
    qtype: QuestionType = QuestionType.UNKNOWN
    #: 页面上的题型标记（``【多选题】`` 里的那几个字），排障时有用
    qtype_hint: str = ""

    @property
    def fingerprint(self) -> str:
        """题干指纹（题库缓存的键）。"""
        return question_hash(self.stem)

    @property
    def labels(self) -> list[str]:
        """全部选项标签（``["A", "B", …]``）。"""
        return [option.label for option in self.options if option.label]

    @property
    def answerable(self) -> bool:
        """是否具备作答条件。

        判断 / 填空 / 简答靠**文本**作答，不需要选项；选择题则必须有带标签的选项 ——
        选项没解析出来时宁可判为不可作答，也不要让模型去猜一道看不到选项的题，
        更不要把猜测结果提交上去。连线题必须先把左项解析出来（否则没有可填的序号）。
        """
        if self.qtype == QuestionType.MATCHING:
            return bool(self.pairs)
        if self.qtype in {QuestionType.JUDGE, QuestionType.FILL, QuestionType.SHORT}:
            return True
        return bool(self.labels)


class Answer(ChaoxingModel):
    """一道题的答案。"""

    #: 题干指纹（与 :meth:`Question.fingerprint` 一致）
    fingerprint: str = ""
    #: 归一化后的答案值：单选 ``"A"``、多选 ``"AB"``、判断 ``"对"``
    value: str = ""
    #: 答案来源：cache / model / manual
    source: str = "model"
    confidence: float = 0.0
    #: 模型给出的理由（便于人工复核与排障）
    reason: str = ""

    @property
    def usable(self) -> bool:
        """是否可用（空答案一律判为不可用）。"""
        return bool(self.value.strip())


def normalize_answer(raw: str, *, qtype: QuestionType) -> str:
    """把模型的输出归一化成学习通能接受的值。

    实测各题型的提交格式不同，这里一次说清：

    * 单选 / 多选：选项字母，多选**必须去重并升序**（``"ba"`` → ``"AB"``）；
    * 判断：``"对"`` / ``"错"``（也接受 ``true/false``、``√/×``）；
    * 连线：原样保留（``"1=A"`` 这类多行映射由提交层再解析成 JSON）；
    * 填空 / 简答：原样保留（含标点的答案就是答案）。

    :param raw: 原始答案
    :param qtype: 题型
    :returns: 归一化后的答案
    """
    text = squish(raw)
    if not text:
        return ""

    if qtype in {QuestionType.SINGLE, QuestionType.MULTIPLE}:
        letters = sorted({char.upper() for char in text if char.isalpha() and char.isascii()})
        return "".join(letters)

    if qtype == QuestionType.JUDGE:
        lowered = text.lower()
        if lowered in {"对", "正确", "true", "t", "yes", "y", "√", "a"}:
            return "对"
        if lowered in {"错", "错误", "false", "f", "no", "n", "×", "x", "b"}:
            return "错"
        return text

    return text
