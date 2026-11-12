"""课程列表解析。

实测结构（``tests/fixtures/course_list.html``，来自 ``/visit/courselistdata``）::

    <ul class="course-list" id="courseList">
      <li class="course clearfix" courseId="265361351" clazzId="151148348"
          personId="100000001" id="course_265361351_151148348">
        <div class="course-cover">
          <a href="https://mooc1.chaoxing.com/mooc-ans/visit/stucoursemiddle?...">

两个结论：

* **ID 就在 ``li.course`` 的属性里**（比从 URL 里抠 query 可靠）；
* 编码随版本变化（早先 GBK、现为 UTF-8）—— 解码在 API 层按响应头做，本层只吃已解码文本。
"""

from __future__ import annotations

from bs4 import BeautifulSoup
from bs4.element import Tag

from chaoxing.client.model.course import Course
from chaoxing.client.parser.common import attr

#: 课程卡片标题的候选选择器（版本间会漂移）
_TITLE_SELECTORS = (
    ".course-name",
    ".course-name a",
    "[class*=course-name]",
    ".course-info .name",
)


def _course_title(node: Tag) -> str:
    """取课程标题：先试已知选择器，退化到链接的 title 属性。"""
    for selector in _TITLE_SELECTORS:
        found = node.select_one(selector)
        if isinstance(found, Tag):
            text = found.get_text(strip=True)
            if text:
                return text
    anchor = node.find("a", href=True)
    if isinstance(anchor, Tag):
        return str(anchor.get("title") or "").strip()
    return ""


def parse_course_list(html: str) -> list[Course]:
    """解析"我的课程"页面，返回可用课程（缺少 ID 的卡片直接丢弃）。

    :param html: 已解码的课程列表 HTML
    :returns: 课程列表（按页面顺序，已按 ``courseId:clazzId`` 去重）
    """
    if not html.strip():
        return []

    soup = BeautifulSoup(html, "lxml")
    courses: list[Course] = []
    seen: set[str] = set()

    for node in soup.select("li.course"):
        # 用 model_validate 而不是关键字构造：字段别名（courseId / clazzId / personId）
        # 是"线上真实叫法"，构造函数只认字段名，走 validate 两条路都通。
        course = Course.model_validate(
            {
                "courseId": attr(node, "courseId", "courseid", "course_id"),
                "clazzId": attr(node, "clazzId", "clazzid", "clazz_id"),
                "personId": attr(node, "personId", "personid", "person_id"),
                "courseName": _course_title(node),
            }
        )
        if not course.is_complete or course.key in seen:
            continue
        seen.add(course.key)
        courses.append(course)

    return courses


def parse_course_entry_params(href: str) -> dict[str, str]:
    """从课程入口链接里抠出 ``courseid`` / ``clazzid`` / ``cpi`` 三个参数。

    作为 ``li.course`` 属性缺失时的兜底通道（老版页面只给链接，不给属性）。

    :param href: 形如 ``.../stucoursemiddle?courseid=..&clazzid=..&cpi=..``
    :returns: 参数名 -> 值（缺失的键不会出现）
    """
    from urllib.parse import parse_qsl, urlparse

    wanted = {"courseid", "clazzid", "cpi", "personid", "vc", "enc"}
    query = urlparse(href).query
    return {
        key.lower(): value for key, value in parse_qsl(query) if key.lower() in wanted and value
    }
