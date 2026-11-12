"""明文题解析（绕开字体反爬的那条渠道）。

来源：legacy 实测 —— ``/api/selectWorkQuestion`` 返回的题目**没有字体加密**，
但结构与题目页不同：题干在 ``.Zy_TItle_p`` 的段落里（**最长的那一段**是关键，
题型标记与应用文都在同容器里），题型标记写成 ``【多选题】``。

两个必须记住的性质：

1. 返回顺序与题目页**一致**，因此可以按索引与另一侧（带题目 ID 的页面）合并；
2. 选项文本取 ``<a>`` 里的内容（``<i>`` 里只有 ``A、`` 这样的标签）。
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from chaoxing.client.model.quiz import Question, QuestionOption, QuestionType
from chaoxing.client.parser.chapter import strip_tags

#: 题型线索词（按优先级判断；学习通各版本用词不完全一致）
_MULTI_HINTS = ("多选", "多项", "不定项")
_SINGLE_HINTS = ("单选", "单项")
_JUDGE_HINTS = ("判断", "对错")
_MATCH_HINTS = ("连线",)
_FILL_HINTS = ("填空",)
_SHORT_HINTS = ("简答", "论述", "问答", "名词解释")

_HINT_PATTERN = re.compile(r"【([^】]{1,12})】")
_LABEL_PATTERN = re.compile(r"^\s*([A-Za-z])")
_DIGITS_PATTERN = re.compile(r"\d+")


def guess_question_type(text: str, option_count: int) -> QuestionType:
    """按题干文案 + 选项数量推断题型。

    :param text: 题干与页面标记拼起来的文本
    :param option_count: 选项数量
    :returns: :class:`~chaoxing.client.model.quiz.QuestionType`
    """
    hay = text or ""
    if any(hint in hay for hint in _MULTI_HINTS):
        return QuestionType.MULTIPLE
    if any(hint in hay for hint in _SINGLE_HINTS):
        return QuestionType.SINGLE
    if any(hint in hay for hint in _JUDGE_HINTS):
        return QuestionType.JUDGE
    if any(hint in hay for hint in _MATCH_HINTS):
        return QuestionType.MATCHING
    if any(hint in hay for hint in _FILL_HINTS):
        return QuestionType.FILL
    if any(hint in hay for hint in _SHORT_HINTS):
        return QuestionType.SHORT
    if option_count == 2:
        # 只有两个选项又没有题型标记时，判断题的可能性最大（实测常见）
        return QuestionType.JUDGE
    if option_count >= 2:
        return QuestionType.SINGLE
    return QuestionType.UNKNOWN


def _extract_stem(title_node: object) -> tuple[str, str]:
    """从标题容器里取出（题干, 题型标记）。"""
    text = title_node.get_text(" ", strip=True)  # type: ignore[attr-defined]
    hint_match = _HINT_PATTERN.search(text)
    hint = hint_match.group(1) if hint_match else ""

    paragraphs = [
        paragraph.get_text(" ", strip=True)
        for paragraph in title_node.find_all("p")  # type: ignore[attr-defined]
    ]
    candidates = [item for item in paragraphs if len(item) >= 4]
    stem = max(candidates, key=len) if candidates else text
    stem = _HINT_PATTERN.sub("", strip_tags(stem)).strip()
    return stem, hint


def _item_text(item: Any) -> str:
    """取一个选项 / 左项的可读文本（``<a>`` 里的内容，``<i>`` 里只有标签）。"""
    anchor = item.find("a") or item
    return strip_tags(anchor.get_text(" ", strip=True)).strip()


def _extract_options(node: object) -> list[QuestionOption]:
    """从题目容器里取出选项。"""
    options: list[QuestionOption] = []
    for index, item in enumerate(node.select("ul.Zy_ulTop li, ul li")):  # type: ignore[attr-defined]
        label_node = item.find("i")
        label = ""
        if label_node is not None:
            match = _LABEL_PATTERN.match(label_node.get_text(" ", strip=True))
            label = match.group(1).upper() if match else ""
        text = _item_text(item)
        if not text:
            continue
        if not label:
            label = chr(ord("A") + index) if index < 26 else f"O{index}"
        options.append(QuestionOption(label=label, text=text))
    return options


def _matching_items(group: Any) -> list[Any]:
    """取连线某一组里的项（**跳过组标题** ``li.groupTitile``）。"""
    return [
        item
        for item in group.find_all("li", recursive=False)
        if "groupTitile" not in (item.get("class") or [])
    ]


def _extract_matching(node: Any) -> tuple[list[tuple[str, str]], list[QuestionOption]] | None:
    """把连线题的两组拆开：左项（序号 + 文本）与右项（字母选项）。

    明文页的连线题是**两个 ``ul``**：``ul.firstUlList`` 是左项（``1、2、…`` 编号），
    ``ul.secondUlList`` 是右项（``A、B、…`` 字母）；两组各自以 ``li.groupTitile``
    （组标题，如「第一组数据」）开头。整段按普通选项解析会把组标题也当成选项
    （真机实测：12 个"选项"里混进了 2 个组标题，且字母从头重排）。

    结构对不上时返回 ``None`` —— 调用方退回通用解析，绝不臆造。

    :param node: 题目容器
    :returns: ``(pairs, options)``；不是可识别的连线题结构时为 ``None``
    """
    left = node.select_one("ul.firstUlList")
    right = node.select_one("ul.secondUlList")
    if left is None or right is None:
        return None

    pairs: list[tuple[str, str]] = []
    for item in _matching_items(left):
        text = _item_text(item)
        if not text:
            continue
        label_node = item.find("i")
        raw = label_node.get_text(" ", strip=True) if label_node is not None else ""
        digits = _DIGITS_PATTERN.search(raw)
        name = digits.group(0) if digits else str(len(pairs) + 1)
        pairs.append((name, text))

    options: list[QuestionOption] = []
    for index, item in enumerate(_matching_items(right)):
        text = _item_text(item)
        if not text:
            continue
        label_node = item.find("i")
        label = ""
        if label_node is not None:
            match = _LABEL_PATTERN.match(label_node.get_text(" ", strip=True))
            label = match.group(1).upper() if match else ""
        if not label:
            label = chr(ord("A") + index) if index < 26 else f"O{index}"
        options.append(QuestionOption(label=label, text=text))

    if not pairs or not options:
        return None
    return pairs, options


def parse_plain_questions(html: str) -> list[Question]:
    """解析明文题目（``selectWorkQuestion`` 的响应）。

    :param html: 响应正文
    :returns: 题目列表（顺序与页面一致）
    """
    soup = BeautifulSoup(html or "", "lxml")
    questions: list[Question] = []

    for node in soup.select("div.TiMu, div.newTiMu"):
        title_node = node.select_one(".Zy_TItle_p, .Zy_TItle, .mark_name")
        if title_node is None:
            continue
        stem, hint = _extract_stem(title_node)
        if not stem:
            continue
        options = _extract_options(node)
        pairs: list[tuple[str, str]] = []
        if any(item in hint for item in _MATCH_HINTS):
            matched = _extract_matching(node)
            if matched is not None:
                pairs, options = matched
        questions.append(
            Question(
                index=len(questions),
                stem=stem,
                options=options,
                pairs=pairs,
                qtype=guess_question_type(f"{hint} {stem}", len(options)),
                qtype_hint=hint,
            )
        )

    return questions


def merge_question_ids(questions: list[Question], ids: list[str]) -> list[Question]:
    """把题目 ID 按**索引**补进明文题（两侧顺序一致，这是实测性质）。

    :param questions: 明文题列表
    :param ids: 题目 ID 列表
    :returns: 补好 ID 的新列表（数量不匹配时只补能对上的部分）
    """
    for index, question in enumerate(questions):
        if index < len(ids) and ids[index]:
            question.question_id = ids[index]
    return questions


#: 页面 ``answertype{id}`` → 题型（真机 2026-10-16：0 单选 / 1 多选 / 3 判断）
_ANSWERTYPE_TO_QTYPE = {
    "0": QuestionType.SINGLE,
    "1": QuestionType.MULTIPLE,
    "2": QuestionType.FILL,
    "3": QuestionType.JUDGE,
    "4": QuestionType.SHORT,
    "5": QuestionType.MATCHING,
}


def align_questions_to_ids(
    questions: list[Question], ids: list[str], page_types: dict[str, str]
) -> list[Question]:
    """按**题型**把题目 ID 配给明文题（替代"按索引"）。

    真机实测（2026-10-16，账号 2 课程 24）：明文题与题目页的顺序**并不总是相同**；
    按索引合并会把判断题配上多选的答案 —— 服务端**整卷拒绝**（只回笼统的
    「作业提交失败！」）。这里按页面 ``answertype{id}`` 与明文题题型做**保序匹配**：
    同题型优先配对；配不上的再按剩余顺序补齐（保持旧行为"只补能对上的部分"）。

    :param questions: 明文题列表（顺序为明文页顺序）
    :param ids: 题目 ID 列表（顺序为题目页顺序）
    :param page_types: ``{"401151530": "0", …}``（来自页面隐藏域 ``answertype{id}``）
    :returns: 原地补好 ``question_id`` 的同一列表
    """
    expected = [
        _ANSWERTYPE_TO_QTYPE.get(page_types.get(question_id, ""), QuestionType.UNKNOWN)
        for question_id in ids
    ]
    assigned: dict[int, str] = {}
    used_ids: set[str] = set()
    for position, question_id in enumerate(ids):
        want = expected[position]
        if want is QuestionType.UNKNOWN:
            continue
        for index, question in enumerate(questions):
            if index not in assigned and question.qtype is want:
                assigned[index] = question_id
                used_ids.add(question_id)
                break
    leftovers = [question_id for question_id in ids if question_id not in used_ids]
    for index in range(len(questions)):
        if index in assigned:
            continue
        if leftovers:
            assigned[index] = leftovers.pop(0)
    for index, question_id in assigned.items():
        questions[index].question_id = question_id
    return questions


#: 交卷完成的页面标记（学习通各版本用词不同，都要认）
_SUBMITTED_MARKERS = ("已交卷", "已提交", "已完成本次", "查看答案", "已批阅")


def quiz_already_submitted(html: str) -> bool:
    """页面是否显示"这份作业已经交过了"。

    重跑刷课任务时必须先判这个 —— 否则会对已交卷的作业再提交一次，
    轻则无效，重则被判重复提交。

    :param html: 题目页 HTML
    :returns: 是否已提交
    """
    hay = html or ""
    return any(marker in hay for marker in _SUBMITTED_MARKERS)


#: 题目 ID 的候选形态（页面版本不同，写在 id 或 name 上）
_QUESTION_ID_PATTERNS = (
    re.compile(r'id\s*=\s*["\']question(\d+)["\']'),
    re.compile(r'name\s*=\s*["\']answer(\d+)["\']'),
    # 真机（2026-11-10，账号 9 的 19 题测验）：题目容器没有 id=questionN，
    # 只有逐题的 answertypeN 隐藏域 —— 漏掉它就只能认出前 5 题，交卷时静默漏答。
    re.compile(r'name\s*=\s*["\']answertype(\d+)["\']'),
    re.compile(r'data-questionid\s*=\s*["\'](\d+)["\']'),
)


def extract_question_ids(html: str) -> list[str]:
    """从题目页抽出题目 ID（按页面顺序）。

    提交流程有两处都要它：``answer{id}`` 的字段名与 ``answerwqbid`` 列表 ——
    ID 缺一个，提交就会被判"无效的参数：code-1！"。

    :param html: 题目页 HTML
    :returns: 题目 ID 列表（去重并保持顺序）
    """
    if not html:
        return []
    matches: list[tuple[int, str]] = []
    # 四种写法**都要扫**：同一份作业里不同题可能用不同写法（题目页混排很常见），
    # 只认第一种会漏掉一部分 ID，提交时就成了"无效的参数：code-1！"。
    # 必须按**原文位置**排序，不能按 pattern 顺序拼接：真机页面前 5 题有
    # id=questionN、其余题只有 answertypeN，按 pattern 拼会把后半段提前，
    # 与明文题的题型对齐错位。同一 ID 多写法出现时保留第一次出现的位置。
    for pattern in _QUESTION_ID_PATTERNS:
        for match in pattern.finditer(html):
            matches.append((match.start(), match.group(1)))
    found: list[str] = []
    seen: set[str] = set()
    for _, value in sorted(matches, key=lambda item: item[0]):
        if value not in seen:
            seen.add(value)
            found.append(value)
    return found


def extract_hidden_inputs(html: str) -> dict[str, str]:
    """抽出页面上的隐藏域。

    提交时必须**原样回传**题目页上的隐藏字段（``courseId`` / ``classId`` / ``token``…），
    自己拼一份是不行的 —— 服务端会校验这些签名材料。

    :param html: 题目页 HTML
    :returns: ``{name: value}``（同名取最后一个）
    """
    soup = BeautifulSoup(html or "", "lxml")
    fields: dict[str, str] = {}
    for node in soup.select("input[type=hidden]"):
        name = str(node.get("name") or "").strip()
        if not name:
            continue
        fields[name] = str(node.get("value") or "")
    return fields


def extract_form_action(html: str, form_id: str = "form1") -> str:
    """抽出提交表单的 ``action`` 原文（相对路径保持相对）。

    真机实测（2026-10-16）：浏览器交卷打的是**表单自己的 action** ——
    ``addStudentWorkNewWeb?_classId=…&courseid=…&token=…&totalQuestionNum=…``，
    其中 ``token`` 正是隐藏域里的 ``enc_work``。自己拼查询串（``api`` / ``enc`` /
    ``pos`` / ``rd`` / ``wid`` / ``_edt`` 那一套）**字段集全对也没用** —— 服务端一律回
    「作业提交失败！」；键名（``_classId``/``courseid``/``workid``/``testmooc2``）
    与取值都必须照表单原样。

    :param html: 题目页 HTML
    :param form_id: 提交表单的 id（题目页固定为 ``form1``）
    :returns: action 原文；页面没有该表单时返回空串
    """
    soup = BeautifulSoup(html or "", "lxml")
    form = soup.find("form", id=form_id)
    if form is None:
        return ""
    return str(form.get("action") or "").strip()
