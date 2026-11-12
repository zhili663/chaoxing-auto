"""章节完成状态解析（`parse_chapter_progress`）。

补这个能力的原因是实测出来的：本地 `task_points.status` 把 71 个任务点记成待刷，
而学习通侧**早已完成**（70 视频 + 1 测验）。后果是刷课编排反复去撞这些点 ——
既发无效请求，也永远等不到心跳，因为**服务端不会为已完成的任务点产生上报**。

而完成标记**不在卡片响应里**（实测两个章节的卡片都搜不到「已完成」），
只在**课程目录页**上：``div.catalog_state.icon_yiwanc``。
"""

from __future__ import annotations

from chaoxing.client.parser.chapter import parse_chapter_progress

#: 真实目录页片段的骨架（结构照抄线上，内容精简）
INDEX_HTML = """
<div class="chapter_unit">
  <ul>
    <li>
      <div class="chapter_item" id="cur757458172" title="人格谜题">
        <div class="catalog_title">
          <div class="inputCheck fl"><input type="checkbox" name="checkbox" value="757458172"/></div>
          <div class="catalog_name newCatalog_name"><a class="clicktitle"><span>1.1</span> 人格谜题</a></div>
          <div class="catalog_task">
            <div class="catalog_state icon_yiwanc catalog_tishi56"><span>已完成<i></i></span></div>
          </div>
        </div>
      </div>
    </li>
    <li>
      <div class="chapter_item" id="cur757458183" title="人格的特征">
        <div class="catalog_title">
          <div class="inputCheck fl"><input type="checkbox" name="checkbox" value="757458183"/></div>
          <div class="catalog_name newCatalog_name"><a class="clicktitle"><span>1.2</span> 人格的特征</a></div>
          <div class="catalog_task">
            <div class="catalog_state icon_weiwanc catalog_tishi57"><span>待完成<i></i></span></div>
          </div>
        </div>
      </div>
    </li>
    <li>
      <div class="chapter_item" id="cur904575399" title="作业一">
        <div class="catalog_title">
          <div class="inputCheck fl"><input type="checkbox" name="checkbox" value="904575399"/></div>
          <div class="catalog_name newCatalog_name"><a class="clicktitle">1.6 作业一</a></div>
          <div class="catalog_task">
            <div class="catalog_state icon_weiwanc catalog_tishi57"><span>待完成<i></i></span></div>
          </div>
        </div>
      </div>
    </li>
  </ul>
</div>
"""


def test_parse_chapter_progress_marks_done_and_pending() -> None:
    """已完成与未完成要分得清 —— 这是"只挑真正未完成的点"的唯一依据。"""
    progress = parse_chapter_progress(INDEX_HTML)

    assert progress == {"757458172": True, "757458183": False, "904575399": False}


def test_parse_chapter_progress_handles_empty_input() -> None:
    """空输入返回空字典，而不是抛异常（目录页偶尔取不到）。"""
    assert parse_chapter_progress("") == {}


def test_parse_chapter_progress_ignores_items_without_checkbox() -> None:
    """没有 checkbox 的项跳过 —— 目录页里还有纯标题、分隔线这类节点。"""
    html = """
    <div class="catalog_title"><div class="catalog_name">第一级标题（无 checkbox）</div></div>
    <div class="chapter_item">
      <input type="checkbox" name="checkbox" value="abc-not-a-number"/>
      <div class="catalog_state icon_yiwanc"></div>
    </div>
    """

    assert parse_chapter_progress(html) == {}


def test_parse_chapter_progress_on_real_index_page() -> None:
    """真实目录页（线上抓取，已精简到必要结构）应当解析出上百个章节。

    样本见 ``tests/fixtures/course_index.html``：从真实 ``studentcourse`` 页面
    里截取的章节骨架，保留 ``chapter_item`` / ``checkbox`` / ``catalog_state`` 三层。
    """
    from pathlib import Path

    sample = Path(__file__).parent.parent / "fixtures" / "course_index.html"
    if not sample.exists():
        return  # 样本未入库时跳过（不制造假失败）

    progress = parse_chapter_progress(sample.read_text(encoding="utf-8"))

    assert len(progress) > 50, "真实目录页应当解析出上百个章节"
    assert any(progress.values()), "真实目录页里一定有已完成项"
    assert not all(progress.values()), "真实目录页里一定有未完成项"
