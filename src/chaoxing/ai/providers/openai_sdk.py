"""OpenAI 兼容接口实现（`openai` + `instructor` 结构化输出）。

**只有这个文件**碰第三方 SDK：换库时上层零改动。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from chaoxing.ai.prompts import DEFAULT_SYSTEM_PROMPT, render_question
from chaoxing.client.model.quiz import Answer, Question, normalize_answer

if TYPE_CHECKING:  # 仅为类型：检索实现不进本模块的运行时导入
    from chaoxing.ai.search import Snippet


class AnswerPayload(BaseModel):
    """模型返回的结构（结构化输出，避免解析自由文本）。"""

    answer: str = Field(default="", description="按系统提示要求的格式给出答案")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = Field(default="", description="一句话理由")


class OpenAIProvider:
    """OpenAI 兼容接口作答（instructor 保证结构化输出）。

    :param base_url: 兼容端点（OpenAI / DeepSeek / 自建网关都行）
    :param api_key: 密钥
    :param model: 模型名
    :param system_prompt: 系统提示词
    :param timeout: 单次请求超时（秒）
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._system_prompt = system_prompt
        self._timeout = timeout

    @property
    def system_prompt(self) -> str:
        """当前使用的系统提示词（诊断与测试用）。"""
        return self._system_prompt

    async def answer(
        self, question: Question, *, snippets: Sequence[Snippet] | None = None
    ) -> Answer:
        """调用模型作答。

        :param question: 题目
        :param snippets: 可选的网络检索片段（拼进用户消息，辅助作答）
        :raises RuntimeError: 依赖缺失或模型返回不可用
        :returns: :class:`~chaoxing.client.model.quiz.Answer`
        """
        try:
            import instructor
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - 依赖在则不会发生
            raise RuntimeError("缺少 openai / instructor 依赖，无法调用模型") from exc

        client = instructor.from_openai(
            AsyncOpenAI(base_url=self._base_url, api_key=self._api_key, timeout=self._timeout)
        )
        payload: AnswerPayload = await client.chat.completions.create(
            model=self._model,
            response_model=AnswerPayload,
            temperature=0,
            messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": render_question(question, snippets)},
            ],
        )
        value = normalize_answer(payload.answer, qtype=question.qtype)
        return Answer(
            fingerprint=question.fingerprint,
            value=value,
            source="model",
            confidence=payload.confidence,
            reason=payload.reason,
        )
