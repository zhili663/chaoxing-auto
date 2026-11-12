"""章节域：任务点卡片与章节内容页。"""

from __future__ import annotations

import logging
from typing import Any

from chaoxing.client.api.base import ApiModule, decode_response
from chaoxing.client.model.base import ensure_not_error_page
from chaoxing.client.model.card import JobCard
from chaoxing.client.parser.card import extract_cards_payload, parse_job_card
from chaoxing.client.session import SessionState

log = logging.getLogger(__name__)


class ChapterApi(ApiModule):
    """章节内容与任务点卡片。"""

    async def fetch_cards_payload(
        self,
        session: SessionState,
        *,
        course_id: str,
        clazz_id: str,
        knowledge_id: str,
        cpi: str = "",
        stuenc: str = "",
        num: int = 0,
    ) -> dict[str, Any] | None:
        """取章节卡片原始 JSON。

        **与章节内容页一样必须带 ``stuenc``** —— 少了它服务端给
        ``[3004] 长时间没有操作或没有权限``。这一条是真账号才踩出来的：
        章节内容页当时带上了，卡片请求漏了，于是"章节能扫、卡片全废"。

        ★ ``num`` 是**任务点在章节内的序号**（0 基）：实测 ``num=0`` 是视频、
        ``num=1`` 是章节测验 —— 同一章节的多个任务点共用一个接口，只靠它区分。
        只取默认值会漏掉视频之后的所有任务点（详见
        :func:`~chaoxing.client.parser.chapter.parse_chapter_task_counts`）。

        :param session: 账号会话
        :param course_id: 课程 ID
        :param clazz_id: 班级 ID
        :param knowledge_id: 章节（知识点）ID
        :param cpi: 用户在课程下的 ID
        :param stuenc: 课程入口签发的 enc
        :param num: 任务点在章节内的序号（0 基）
        :returns: 卡片 JSON；响应不可解析时返回 ``None``
        """
        response = await self._get(
            session,
            "chapter_cards",
            params={
                "courseid": course_id,
                "clazzid": clazz_id,
                "knowledgeid": knowledge_id,
                "cpi": cpi,
                "stuenc": stuenc,
                "num": str(num),
            },
        )
        text = decode_response(response)
        ensure_not_error_page(text)
        return extract_cards_payload(text)

    async def fetch_cards(
        self,
        session: SessionState,
        *,
        course_id: str,
        clazz_id: str,
        knowledge_id: str,
        cpi: str = "",
        stuenc: str = "",
        num: int = 0,
    ) -> JobCard | None:
        """取并解析任务点卡片（附件已摊平 ``property`` 并按身份键归并）。

        :param num: 任务点在章节内的序号（0 基；见 :meth:`fetch_cards_payload`）
        :returns: :class:`~chaoxing.client.model.card.JobCard`；无数据时 ``None``
        """
        payload = await self.fetch_cards_payload(
            session,
            course_id=course_id,
            clazz_id=clazz_id,
            knowledge_id=knowledge_id,
            cpi=cpi,
            stuenc=stuenc,
            num=num,
        )
        if payload is None:
            log.warning("章节 %s 的卡片响应无法解析", knowledge_id)
            return None
        return parse_job_card(payload)

    async def fetch_content_html(
        self,
        session: SessionState,
        *,
        course_id: str,
        clazz_id: str,
        cpi: str,
        stuenc: str,
        timestamp: str = "",
    ) -> str:
        """取章节内容页 HTML。

        **必须带 ``stuenc`` 与 ``t``**（来自课程入口的 302 地址），否则服务端返回权限错误。

        :param session: 账号会话
        :param course_id: 课程 ID
        :param clazz_id: 班级 ID
        :param cpi: 用户在课程下的 ID
        :param stuenc: 课程入口签发的 enc
        :param timestamp: 页面上的 ``t`` 参数（可为空串）
        :returns: 章节页 HTML
        """
        response = await self._get(
            session,
            "chapter_content",
            params={
                "courseid": course_id,
                "clazzid": clazz_id,
                "cpi": cpi,
                "stuenc": stuenc,
                "t": timestamp,
            },
        )
        text = decode_response(response)
        ensure_not_error_page(text)
        return text
