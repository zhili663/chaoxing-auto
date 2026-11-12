"""章节树解析：从课程学习页里抽出章节列表。

来源：legacy 实测 —— 新版页面把章节数据写在 JS 变量里（``chapterId`` / ``chapterName``
成对出现），老版是 ``<div class="chapter_item">`` 这类 DOM；**两条路都要走**，
因为同一账号名下不同课程用的页面版本并不一致。
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from chaoxing.client.model.course import Chapter

#: 章节数据内嵌在 JS 里时的形状（chapterId/chapterName 或 knowledgeId/name）
_JS_PATTERN = re.compile(
    r'"(?:chapterId|knowledgeId|id)"\s*:\s*"?(\d+)"?[^}]{0,200}?'
    r'"(?:chapterName|name|title)"\s*:\s*"([^"]{1,120})"',
    re.S,
)

#: 老版 DOM 的候选选择器
_DOM_SELECTORS = (
    "div.chapter_item",
    "div.chapter_unit",
    "li.chapter_item",
    "div.catalog_level",
    "div[id^='chapter']",
)

_TAG_PATTERN = re.compile(r"<[^>]+>")


def strip_tags(text: str) -> str:
    """去掉 HTML 标签并压掉多余空白。"""
    return " ".join(_TAG_PATTERN.sub(" ", text or "").split())


def parse_chapter_tree(html: str) -> list[Chapter]:
    """解析课程学习页的章节树。

    :param html: 学习页 HTML
    :returns: 章节列表（按页面出现顺序；解析不出时返回空列表）
    """
    if not html:
        return []
    return _from_js(html) or _from_dom(html)


def _from_js(html: str) -> list[Chapter]:
    """从内联 JSON/JS 里抽章节（新版页面）。"""
    chapters: list[Chapter] = []
    seen: set[str] = set()
    for match in _JS_PATTERN.finditer(html):
        chapter_id, name = match.group(1), strip_tags(match.group(2))
        if not chapter_id or not name or chapter_id in seen:
            continue
        seen.add(chapter_id)
        chapters.append(Chapter(chapter_id=chapter_id, name=name, index=len(chapters)))
    return chapters


def _from_dom(html: str) -> list[Chapter]:
    """从 DOM 结构抽章节（老版 / 兜底）。"""
    soup = BeautifulSoup(html, "lxml")
    chapters: list[Chapter] = []
    seen: set[str] = set()

    for selector in _DOM_SELECTORS:
        for node in soup.select(selector):
            link = node.find("a", href=True)
            raw = str(link["href"]) if link else str(node.get("data-chapterid") or "")
            chapter_id = ""
            if raw:
                found = re.search(r"[?&]chapter[iI]d=(\d+)", raw)
                chapter_id = found.group(1) if found else ""
            if not chapter_id:
                chapter_id = re.sub(r"\D", "", str(node.get("data-id") or node.get("id") or ""))
            name = strip_tags(str(node.get("title") or node.get_text(" ", strip=True) or ""))
            if not chapter_id or not name or chapter_id in seen:
                continue
            seen.add(chapter_id)
            chapters.append(Chapter(chapter_id=chapter_id, name=name, index=len(chapters)))
        if chapters:
            break

    return chapters


#: 目录页里表示"已完成"的 class
_PROGRESS_DONE_CLASS = "icon_yiwanc"


def parse_chapter_progress(html: str) -> dict[str, bool]:
    """从课程目录页解析「章节 ID → 是否已完成」。

    目录页（``mooc2-ans/mycourse/studentcourse``）里每一项长这样::

        <div class="chapter_item" id="cur757458172" ...>
          <input type="checkbox" name="checkbox" value="757458172"/>
          ...
          <div class="catalog_state icon_yiwanc catalog_tishi56"><span>已完成</span></div>
        </div>

    章节 ID 在 checkbox 的 ``value`` 上，完成状态在 ``catalog_state`` 的 class 上
    （``icon_yiwanc`` = 已完成）。

    为什么需要它：**卡片响应里没有任何完成标记**（实测两个章节的卡片都搜不到），
    而刷课编排必须知道"哪些点其实早做完了" —— 否则会反复去撞已完成的点，
    既发无效请求，也永远等不到心跳（**服务端不会为已完成的任务点产生上报**）。

    :param html: 目录页 HTML
    :returns: ``{chapter_id: 是否已完成}``；解析不出时返回空字典
    """
    if not html:
        return {}

    soup = BeautifulSoup(html, "lxml")
    result: dict[str, bool] = {}
    for box in soup.find_all("input", attrs={"name": "checkbox"}):
        chapter_id = re.sub(r"\D", "", str(box.get("value") or ""))
        if not chapter_id:
            continue
        holder = box.find_parent(class_="chapter_item") or box.parent
        state = holder.select_one(".catalog_state") if holder else None
        classes = " ".join(str(item) for item in (state.get("class") or [])) if state else ""
        result[chapter_id] = _PROGRESS_DONE_CLASS in classes
    return result
