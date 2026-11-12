"""课程域：课程列表、课程入口（换取 stuenc）。"""

from __future__ import annotations

import logging
from urllib.parse import parse_qsl, urljoin, urlparse

from chaoxing.client.api.base import ApiModule, decode_response
from chaoxing.client.model.course import Course
from chaoxing.client.parser.course import parse_course_list
from chaoxing.client.session import SessionState


def extract_stuenc(url: str) -> str:
    """从课程页的最终 URL 里取出 ``enc``（即后续章节页要用的 **stuenc**）。

    :param url: 课程入口 302 之后的地址
    :returns: enc 值；没有则返回空串
    """
    return dict(parse_qsl(urlparse(url).query)).get("enc", "")


log = logging.getLogger(__name__)

#: 手动跟随重定向的上限（防环）
MAX_REDIRECT_HOPS = 5


class CourseApi(ApiModule):
    """ "我的课程"与课程入口。"""

    async def fetch_list_html(self, session: SessionState) -> str:
        """取课程列表页面。

        **不要写死编码**：早先这条接口确实是 GBK（注释也这么写），但现在响应头
        明写 ``charset=UTF-8``，再按 GBK 解就整页乱码（课程名形如「澶у︾敓」）。
        交给 :func:`decode_response` 按响应头判断。

        :param session: 账号会话
        :returns: 已解码的 HTML
        """
        response = await self._get(session, "course_list")
        return decode_response(response)

    async def list_courses(self, session: SessionState) -> list[Course]:
        """拉取并解析课程列表。

        :param session: 账号会话
        :returns: 课程列表
        """
        return parse_course_list(await self.fetch_list_html(session))

    async def enter_course(
        self,
        session: SessionState,
        *,
        course_id: str,
        clazz_id: str,
        cpi: str = "",
    ) -> str:
        """进入课程：拿回**签着 ``stuenc`` 的地址**。

        这一步是打通后续章节 / 测验链路的**前置条件** —— 直接拼章节页地址会得到
        ``[3004] 长时间没有操作或没有权限``。

        **必须手动逐跳跟随**：``stuenc`` 签在重定向**途中**某一跳的 URL 上，
        而最终落地页并不带它。早先用"跟着跳完再看 ``response.url``"的写法，
        拿到的是空 cpi / 空 stuenc，表现为"章节能扫、卡片接口返回 HTML 而不是 JSON"
        —— 真账号才暴露，mock 里看不出来。

        :param session: 账号会话
        :param course_id: 课程 ID
        :param clazz_id: 班级 ID
        :param cpi: 用户在该课程下的 ID（课程卡片里的 personId）
        :returns: 含 ``stuenc`` 的那一跳地址（实在没有则返回最后一跳）
        """
        response = await self._get(
            session,
            "course_entry",
            params={"courseid": course_id, "clazzid": clazz_id, "cpi": cpi},
            follow_redirects=False,
        )
        current = str(response.url)
        if extract_stuenc(current):
            return current

        for _ in range(MAX_REDIRECT_HOPS):
            location = response.headers.get("location", "")
            if not location:
                break
            nxt: str = str(urljoin(current, str(location)))
            if extract_stuenc(nxt):
                log.debug("课程入口的第 %d 跳带有 enc（即 stuenc）", _ + 1)
                return nxt
            response = await self._transport.request(
                session, nxt, method="GET", follow_redirects=False
            )
            current = str(response.url)

        log.warning("课程 %s 入口没有拿到 enc/stuenc（调用方会撞 [3004]）", course_id)
        return current
