"""模型契约（**唯一自制件**）。

上层只认这个协议：换 SDK、换假实现（测试/离线）都不动业务代码。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from chaoxing.client.model.quiz import Answer, Question

if TYPE_CHECKING:  # 仅为类型：检索实现（httpx）不进 `import chaoxing.ai` 的图
    from chaoxing.ai.search import Snippet


class LLMProvider(Protocol):
    """答题用的大模型契约。"""

    async def answer(
        self, question: Question, *, snippets: Sequence[Snippet] | None = None
    ) -> Answer:
        """给出一道题的答案。

        :param question: 题目
        :param snippets: 可选的网络检索片段（辅助资料，实现可忽略）
        """
        ...
