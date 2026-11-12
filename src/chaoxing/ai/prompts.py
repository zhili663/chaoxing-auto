"""提示词资产：内置默认系统提示词 + 题目渲染。

提示词的**真身**在库里（`prompts` 表，网页可热改，见 `PromptService`）；
这里只是没有覆盖时的兜底默认值。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from chaoxing.client.model.quiz import Question

if TYPE_CHECKING:  # 仅为类型：避免把 httpx 拖进 `import chaoxing.ai` 的图里
    from chaoxing.ai.search import Snippet

#: 提示词里最多放几条检索片段（再多是噪声，还费 token）
MAX_SNIPPETS = 3
#: 标题 / 正文的截断上限（片段是"线索"不是"原文"，不整段搬运）
MAX_TITLE_CHARS = 80
MAX_SNIPPET_CHARS = 240

DEFAULT_SYSTEM_PROMPT = (
    "你是答题助手，只输出 JSON。"
    "answer 字段的格式要求：单选只填一个选项字母（如 A）；"
    "多选把选项字母按升序拼接且不要分隔符（如 ABD）；"
    "判断题只填「对」或「错」；"
    "连线题逐行给出左项序号与右项字母的对应，形如 1=D（每个左项一行）；"
    "填空/简答直接给答案文本。"
    "confidence 是你对这个答案的把握（0~1）。理由写进 reason，一句话以内。"
)


def render_question(question: Question, snippets: Sequence[Snippet] | None = None) -> str:
    """把题目（以及可选的检索片段）渲染成给模型看的文本。

    :param question: 题目
    :param snippets: 网络检索片段（辅助资料，可能无关；留空则只有题目）
    :returns: 形如 ``【多选题】题干\\nA. 选项\\n【网络检索片段…】…``

    连线题会**先列左项**（``1. 外向性``）再列右项（``A. O``）：模型要按左项序号
    逐行作答（``1=D``），看不到左项就没法给序号。
    """
    lines = [f"【{question.qtype_hint or question.qtype.value}】{question.stem}"]
    if question.pairs:
        lines.append("左项：")
        lines.extend(f"{name}. {text}" for name, text in question.pairs)
        lines.append("右项：")
    lines.extend(f"{option.label}. {option.text}" for option in question.options)
    lines.extend(_render_snippets(snippets))
    return "\n".join(lines)


def _render_snippets(snippets: Sequence[Snippet] | None) -> list[str]:
    """把检索片段渲染成提示词行（截断 + 标来源域名）。

    :param snippets: 片段列表
    :returns: 行列表；没有可用片段时为空
    """
    usable = [snippet for snippet in (snippets or []) if snippet.text.strip()][:MAX_SNIPPETS]
    if not usable:
        return []
    lines = ["【网络检索片段（仅作参考，可能与本题无关）】"]
    for index, snippet in enumerate(usable, start=1):
        title = snippet.title.strip()[:MAX_TITLE_CHARS] or "（无标题）"
        text = snippet.text.strip()[:MAX_SNIPPET_CHARS]
        lines.append(f"{index}. {title}（{snippet.domain or '未知来源'}）：{text}")
    return lines
