"""课程列表解析的真实样本回归。

样本：``tests/fixtures/course_list.html``（legacy 抓包
``course_list_mooc1_visit_courselistdata.txt``，来自 ``/visit/courselistdata``）。

为什么值得用真实样本测：课程列表是所有后续动作的入口，**解析错了整条链路都白跑**
（legacy 曾因为把 ``cpi`` 取错而让课程入口 302 到权限错误页）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoxing.client.parser.course import parse_course_entry_params, parse_course_list

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "course_list.html"


@pytest.fixture(scope="module")
def html() -> str:
    """真实课程列表 HTML。"""
    return FIXTURE.read_text(encoding="utf-8")


def test_parses_real_course_list(html: str) -> None:
    """真实样本里必须能解析出结构完整的课程。"""
    courses = parse_course_list(html)

    assert courses, "真实课程列表一门课都没解析出来"
    first = courses[0]
    assert first.course_id.isdigit()
    assert first.clazz_id.isdigit()
    assert first.is_complete


def test_course_key_uses_course_and_class(html: str) -> None:
    """课程键必须是 ``courseId:clazzId``（学习通按"课程+班级"维度定位）。"""
    course = parse_course_list(html)[0]

    assert course.key == f"{course.course_id}:{course.clazz_id}"


def test_cpi_is_taken_from_card_attribute(html: str) -> None:
    """``personId`` 就是后续请求要的 cpi —— 取不到会让课程入口进错页面。"""
    course = parse_course_list(html)[0]

    assert course.cpi, "课程卡片的 personId（cpi）没有解析出来"


def test_title_is_not_empty(html: str) -> None:
    """课程标题必须取到（界面上第一眼看到的东西）。"""
    courses = parse_course_list(html)

    assert all(course.title for course in courses), "有课程没解析出标题"


def test_same_course_is_deduplicated() -> None:
    """同一张卡片重复出现时只保留一条。"""
    card = '<li class="course" courseId="1" clazzId="2" personId="3"></li>'

    assert len(parse_course_list(card + card)) == 1


def test_incomplete_card_is_dropped() -> None:
    """缺 clazzId 的卡片无法发起后续请求，必须丢掉而不是带病前进。"""
    assert parse_course_list('<li class="course" courseId="1"></li>') == []


@pytest.mark.parametrize("garbage", ["", "   ", "<html><body>没有课程</body></html>"])
def test_garbage_input_yields_empty_list(garbage: str) -> None:
    """空页/异常页返回空列表，绝不抛异常打断链路。"""
    assert parse_course_list(garbage) == []


def test_entry_params_fallback_parses_known_keys_only() -> None:
    """老版页面只给链接不给属性时的兜底通道。"""
    href = (
        "https://mooc1.chaoxing.com/mooc-ans/visit/stucoursemiddle"
        "?courseid=265361351&clazzid=151148348&cpi=100000001&ismooc2=1&v=2"
    )

    params = parse_course_entry_params(href)

    assert params["courseid"] == "265361351"
    assert params["clazzid"] == "151148348"
    assert params["cpi"] == "100000001"
    assert "ismooc2" not in params  # 只保留关心的键
