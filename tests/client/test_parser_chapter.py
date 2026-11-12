"""章节树解析：新旧两种页面都要认得。

来源：legacy 实测 —— 新版页面把章节数据写在 JS 变量里（chapterId/chapterName 成对），
老版是 ``<div class="chapter_item">``；同一账号名下不同课程用的页面版本并不一致，
只认一种就会在某些课上"一章都扫不到"。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoxing.client.parser.chapter import parse_chapter_tree, strip_tags

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_parses_chapters_from_js_variables() -> None:
    """新版：章节数据内嵌在 JS 里。"""
    html = """
    <script>
      var chapterList = [{"chapterId": "1192366344", "chapterName": "第一章 绪论", "index": 0},
                         {"chapterId": "1192366345", "chapterName": "第二章 函数", "index": 1}];
    </script>
    """

    chapters = parse_chapter_tree(html)

    assert [item.chapter_id for item in chapters] == ["1192366344", "1192366345"]
    assert chapters[0].name == "第一章 绪论"
    assert chapters[0].index == 0
    assert chapters[1].index == 1


def test_parses_knowledge_id_alias() -> None:
    """同一语义在不同页面里叫 knowledgeId（别名都要认，否则整章扫不到）。"""
    html = '<script>var d = {"knowledgeId": 555, "name": "函数与极限"};</script>'

    chapters = parse_chapter_tree(html)

    assert len(chapters) == 1
    assert chapters[0].chapter_id == "555"


def test_parses_chapters_from_dom_fallback() -> None:
    """老版：DOM 结构（JS 里没有章节数据时兜底）。"""
    html = """
    <div class="chapter_item" id="chapter_1001">
      <a href="/mycourse/studentstudy?chapterId=1001">第一章 绪论</a>
    </div>
    <div class="chapter_item" id="chapter_1002">
      <a href="/mycourse/studentstudy?chapterId=1002">第二章 函数</a>
    </div>
    """

    chapters = parse_chapter_tree(html)

    assert [item.chapter_id for item in chapters] == ["1001", "1002"]
    assert chapters[1].name == "第二章 函数"


def test_duplicate_chapters_are_deduplicated() -> None:
    """同一章节在页面里出现多次时只保留一条。"""
    html = """
    <script>{"chapterId": "1", "chapterName": "第一章"}, {"chapterId": "1", "chapterName": "第一章"}</script>
    """

    assert len(parse_chapter_tree(html)) == 1


def test_returns_empty_for_unparseable_page() -> None:
    """解析不出章节时返回空列表（上层据此报警，而不是当成"这门课没有任务点"）。"""
    assert parse_chapter_tree("") == []
    assert parse_chapter_tree("<html><body>请重新登录</body></html>") == []


def test_strip_tags_cleans_names() -> None:
    """章节名里可能夹着标签与多余空白。"""
    assert strip_tags("  <b>第一章</b>\n  绪论 ") == "第一章 绪论"


@pytest.mark.parametrize("field", ["chapterId", "knowledgeId"])
def test_chapter_id_has_the_expected_type(field: str) -> None:
    """ID 一律按字符串承载（它只是标识符，不参与运算）。"""
    chapters = parse_chapter_tree(f'<script>{{"{field}": "12345", "name": "x"}}</script>')

    assert chapters[0].chapter_id == "12345"
