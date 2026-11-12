"""明文题解析与提交材料抽取。

来源：legacy 实测 —— ``selectWorkQuestion`` 的题干未被字体加密（题目页的题干是
``font-cxsecret`` 加密的，模型读到的会是乱码），因此读题必须走这条渠道；
而**题目 ID 只存在于题目页**，两侧顺序一致所以按索引合并。
"""

from __future__ import annotations

from pathlib import Path

from chaoxing.client.model.quiz import QuestionType
from chaoxing.client.parser.quiz import (
    align_questions_to_ids,
    extract_form_action,
    extract_hidden_inputs,
    extract_question_ids,
    merge_question_ids,
    parse_plain_questions,
    quiz_already_submitted,
)

#: 连线题的真实片段（2026-10-13 从 ``#19`` 明文页截取）：两组各以 ``li.groupTitile`` 开头
MATCHING_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "quiz_matching.html"

#: 与 legacy docstring 里的真实结构一致
PLAIN_HTML = """
<html><body>
<div class="TiMu">
  <div class="Zy_TItle clearfix">
    <i class="fl">1</i>
    <div class="Zy_TItle_p fontLabel">
      【多选题】
      <p><br></p>
      <p>下列属于危化品造成事故的主要特性是（）</p>
    </div>
  </div>
  <ul class="Zy_ulTop">
    <li class="clearfix"><i class="fl">A、</i><a><p>易燃性</p></a></li>
    <li class="clearfix"><i class="fl">B、</i><a><p>扩散性</p></a></li>
    <li class="clearfix"><i class="fl">C、</i><a><p>毒害性</p></a></li>
  </ul>
</div>
<div class="TiMu">
  <div class="Zy_TItle clearfix">
    <i class="fl">2</i>
    <div class="Zy_TItle_p fontLabel">
      【判断题】
      <p>危险化学品仓库应当远离居民区。</p>
    </div>
  </div>
  <ul class="Zy_ulTop">
    <li class="clearfix"><i class="fl">A、</i><a><p>对</p></a></li>
    <li class="clearfix"><i class="fl">B、</i><a><p>错</p></a></li>
  </ul>
</div>
</body></html>
"""


def test_parses_both_question_types() -> None:
    """两道题都要解析出来，题型与选项标签都要对。"""
    questions = parse_plain_questions(PLAIN_HTML)

    assert len(questions) == 2
    assert questions[0].qtype == QuestionType.MULTIPLE
    assert questions[0].stem == "下列属于危化品造成事故的主要特性是（）"
    assert questions[0].labels == ["A", "B", "C"]
    assert questions[1].qtype == QuestionType.JUDGE
    assert questions[1].stem == "危险化学品仓库应当远离居民区。"


def test_stem_prefers_the_longest_paragraph() -> None:
    """题干取最长段落（容器里还混着题型标记与空段落）。"""
    questions = parse_plain_questions(PLAIN_HTML)

    assert "【多选题】" not in questions[0].stem
    assert len(questions[0].stem) > 10


def test_two_options_without_hint_is_judge() -> None:
    """没有题型标记但只有两个选项时按判断题处理（实测常见）。"""
    html = """
    <div class="TiMu"><div class="Zy_TItle_p"><p>这是没有标记的题？</p></div>
      <ul><li><i>A、</i><a>对</a></li><li><i>B、</i><a>错</a></li></ul></div>
    """

    questions = parse_plain_questions(html)

    assert questions[0].qtype == QuestionType.JUDGE


def test_options_without_labels_get_sequential_letters() -> None:
    """页面没给标签时按顺序补 A、B、C（否则这题无法作答）。"""
    html = """
    <div class="TiMu"><div class="Zy_TItle_p"><p>【单选题】题干内容够长了吧？</p></div>
      <ul><li><a>第一项</a></li><li><a>第二项</a></li></ul></div>
    """

    questions = parse_plain_questions(html)

    assert questions[0].labels == ["A", "B"]


def test_matching_question_splits_left_and_right_groups() -> None:
    """连线题：左项与右项两组拆开 —— ``pairs`` 存左项（序号+文本），``options`` 只留右项。

    真机片段（``#19``）：整段按普通选项解析会得到 12 个"选项"（混进 2 个组标题、
    字母从头重排），必须按 ``ul.firstUlList`` / ``ul.secondUlList`` 拆开。
    """
    html = MATCHING_FIXTURE.read_text(encoding="utf-8")

    questions = parse_plain_questions(html)

    assert len(questions) == 1
    question = questions[0]
    assert question.qtype_hint == "连线题"
    assert question.qtype == QuestionType.MATCHING
    assert question.pairs == [
        ("1", "外向性"),
        ("2", "宜人性"),
        ("3", "尽责性"),
        ("4", "神经质"),
        ("5", "开放性"),
    ]
    assert question.labels == ["A", "B", "C", "D", "E"]
    assert [option.text for option in question.options] == ["O", "C", "E", "A", "N"]
    assert question.answerable is True


def test_matching_without_two_groups_is_not_answerable() -> None:
    """结构对不上时不许猜：连线题没解析出左项就判为不可作答（不提交）。"""
    html = (
        '<div class="TiMu"><div class="Zy_TItle_p"><p>【连线题】题干内容够长了吧？</p></div>'
        "<ul><li><i>A、</i><a>甲</a></li><li><i>B、</i><a>乙</a></li></ul></div>"
    )

    questions = parse_plain_questions(html)

    assert questions[0].qtype == QuestionType.MATCHING
    assert questions[0].pairs == []
    assert questions[0].answerable is False


def test_question_ids_are_extracted_in_order() -> None:
    """题目 ID 从题目页抽取（id / name / data-questionid 三种写法）。"""
    html = """
    <div id="question401151530"></div>
    <input name="answer401151531">
    <div data-questionid="401151532"></div>
    <input type="hidden" name="answertype401151533" value="0">
    """

    assert extract_question_ids(html) == [
        "401151530",
        "401151531",
        "401151532",
        "401151533",
    ]


def test_question_ids_mixed_five_plus_rest_in_document_order() -> None:
    """真机 19 题页：前 5 题有 id=questionN，其余只有 answertypeN。"""
    html = "".join(f'<div id="question{1000 + i}"></div>' for i in range(1, 6))
    html += "".join(
        f'<input type="hidden" name="answertype{1000 + i}" value="0">' for i in range(6, 20)
    )

    assert extract_question_ids(html) == [str(1000 + i) for i in range(1, 20)]


def test_hidden_inputs_are_collected() -> None:
    """隐藏域原样收集（提交时必须回传，缺一个就"无效的参数"）。"""
    html = """
    <form>
      <input type="hidden" name="courseId" value="265361351">
      <input type="hidden" name="classId" value="151148348">
      <input type="text" name="noise" value="x">
    </form>
    """

    fields = extract_hidden_inputs(html)

    assert fields == {"courseId": "265361351", "classId": "151148348"}


def test_already_submitted_is_detected() -> None:
    """已交卷的页面必须被识别（重跑时不能再提交一次）。"""
    assert quiz_already_submitted("<div>已交卷</div>") is True
    assert quiz_already_submitted("<div>已批阅</div>") is True
    assert quiz_already_submitted("<div>开始答题</div>") is False


def test_merge_by_index_keeps_question_ids() -> None:
    """按索引把 ID 合并进明文题（两侧顺序一致是实测性质）。"""
    questions = parse_plain_questions(PLAIN_HTML)

    merged = merge_question_ids(questions, ["401151530", "401151531"])

    assert [item.question_id for item in merged] == ["401151530", "401151531"]


def test_merge_tolerates_count_mismatch() -> None:
    """数量对不上时只补能对上的部分（不能整批丢掉）。"""
    questions = parse_plain_questions(PLAIN_HTML)

    merged = merge_question_ids(questions, ["401151530"])

    assert merged[0].question_id == "401151530"
    assert merged[1].question_id == ""


def test_answerable_requires_options() -> None:
    """选项没解析出来的题判为不可作答（宁可留空，也不要瞎猜后提交）。"""
    html = """
    <div class="TiMu"><div class="Zy_TItle_p"><p>【简答题】请论述一下这个问题？</p></div></div>
    """

    questions = parse_plain_questions(html)

    assert questions[0].qtype == QuestionType.SHORT
    assert questions[0].answerable is True  # 简答题靠文本作答

    choice_without_options = parse_plain_questions(
        '<div class="TiMu"><div class="Zy_TItle_p"><p>【单选题】没有选项的题？</p></div></div>'
    )
    assert choice_without_options[0].answerable is False


def test_extract_form_action_reads_form1() -> None:
    """提交目标必须取**表单自己的 action**（键名与取值都与自拼查询串不同）。

    真机实测（2026-10-16）：自己拼查询串（``api`` / ``enc`` / ``pos`` / ``rd`` … 那套）
    字段集全对也没用 —— 服务端一律回「作业提交失败！」；浏览器真正提交的是
    ``version(document.form1.action)``，即 action 原文 + ``version=1``。
    """
    html = (
        '<form id="form1" method="post" '
        'action="addStudentWorkNewWeb?_classId=157322100&amp;token=abc&amp;workid=56918239">'
        '<input type="hidden" name="enc_work" value="abc" /></form>'
    )

    assert extract_form_action(html) == (
        "addStudentWorkNewWeb?_classId=157322100&token=abc&workid=56918239"
    )


def test_extract_form_action_is_empty_without_form() -> None:
    """页面没有 form1（或结构变了）时返回空串，由调用方回退到旧路径。"""
    assert extract_form_action("<html><body>没有表单</body></html>") == ""
    assert extract_form_action("") == ""


def test_align_matches_by_type_not_index() -> None:
    """明文页与题目页顺序不同：必须按题型对齐，而不是按索引。

    真机 2026-10-16：按索引合并把判断题配上了多选答案 → 服务端**整卷拒绝**
    （只回笼统的「作业提交失败！」）。
    """
    questions = parse_plain_questions(
        '<div class="TiMu"><div class="Zy_TItle_p"><p>【判断题】判断一。</p></div></div>'
        '<div class="TiMu"><div class="Zy_TItle_p"><p>【单选题】单选一。</p></div>'
        '<ul class="Zy_ulTop">'
        '<li class="clearfix"><i class="fl">A、</i><a><p>甲</p></a></li>'
        '<li class="clearfix"><i class="fl">B、</i><a><p>乙</p></a></li>'
        "</ul></div>"
    )
    assert [item.qtype for item in questions] == [QuestionType.JUDGE, QuestionType.SINGLE]

    aligned = align_questions_to_ids(questions, ["9001", "9002"], {"9001": "0", "9002": "3"})

    by_id = {item.question_id: item.qtype for item in aligned}
    assert by_id["9001"] is QuestionType.SINGLE
    assert by_id["9002"] is QuestionType.JUDGE


def test_align_falls_back_to_order_for_unknown_types() -> None:
    """页面没给题型时退回按顺序补齐（保持旧行为，不因缺信息而丢 ID）。"""
    questions = parse_plain_questions(
        '<div class="TiMu"><div class="Zy_TItle_p"><p>【判断题】判断一。</p></div></div>'
    )

    aligned = align_questions_to_ids(questions, ["8001"], {})

    assert aligned[0].question_id == "8001"
