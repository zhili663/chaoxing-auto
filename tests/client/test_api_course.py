"""课程域 API 的回归（含 GBK 编码这个真实坑）。"""

from __future__ import annotations

import httpx
import respx

from chaoxing.client import ChaoxingTransport, CourseApi, SessionState
from chaoxing.client.api.course import extract_stuenc
from chaoxing.config import ChaoxingSettings

MOOC1 = "https://mooc1.chaoxing.com"


def _api() -> tuple[ChaoxingTransport, CourseApi]:
    """构造不限速的 transport + CourseApi。"""
    transport = ChaoxingTransport(ChaoxingSettings(request_min_interval=0, max_retry=0))
    return transport, CourseApi(transport)


@respx.mock
async def test_course_list_is_decoded_from_gbk() -> None:
    """实测：``/visit/courselistdata`` 返回 **GBK**；按 UTF-8 解会得到乱码课程名。"""
    html = '<li class="course" courseId="1" clazzId="2" personId="3"><span class="course-name">高等数学</span></li>'
    respx.get(f"{MOOC1}/visit/courselistdata").mock(
        return_value=httpx.Response(200, content=html.encode("gbk"))
    )
    transport, api = _api()

    courses = await api.list_courses(SessionState(account_id=1))

    assert len(courses) == 1
    assert courses[0].title == "高等数学"
    assert courses[0].cpi == "3"
    await transport.aclose()


@respx.mock
async def test_enter_course_returns_final_url_with_stuenc() -> None:
    """课程入口会 302 到带 ``enc``（stuenc）的课程页，调用方要从最终 URL 取值。"""
    respx.get(f"{MOOC1}/mooc-ans/visit/stucoursemiddle").mock(
        return_value=httpx.Response(
            302,
            headers={
                "location": (
                    "https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/stu"
                    "?courseid=1&clazzid=2&enc=stuenc-token&t=1700000000"
                )
            },
        )
    )
    respx.get("https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/stu").mock(
        return_value=httpx.Response(200, text="course page")
    )
    transport, api = _api()

    final_url = await api.enter_course(
        SessionState(account_id=1), course_id="1", clazz_id="2", cpi="3"
    )

    assert extract_stuenc(final_url) == "stuenc-token"
    await transport.aclose()


def test_extract_stuenc_returns_empty_when_absent() -> None:
    """没有 enc 时返回空串（调用方据此判断需要先走课程入口）。"""
    assert extract_stuenc("https://x.com/a?courseid=1") == ""
    assert extract_stuenc("not a url") == ""
