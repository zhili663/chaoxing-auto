"""文档 / PPT / 阅读类任务点。

学习通把"看课件"也计入任务点，但**完成判定不需要真的阅读内容** —— 前端只需调一个
"完成"接口。真实坑（legacy 实测）：

* 缺 ``jtoken`` 时服务端返回 **HTTP 200**，正文却是
  ``{"msg":"无效的请求参数","status":false}`` —— 只判状态码会把失败误报成"已完成"；
* 错误页同样是 HTTP 200，正文是 HTML —— 因此这里用**必须 JSON** 的严格解析，
  非 JSON 直接判失败，不给"假成功"留口子；
* ``jtoken`` 是文档类任务独有令牌（相当于视频的 ``dtoken``），来自章节卡片。
"""

from __future__ import annotations

import logging
import time

from chaoxing.client.api.base import ApiModule
from chaoxing.client.constants import DOMAIN_MOOC1_API
from chaoxing.client.model.base import Envelope, parse_envelope_strict
from chaoxing.client.session import SessionState

log = logging.getLogger(__name__)


class DocumentApi(ApiModule):
    """文档 / PPT / 阅读任务点完成接口。"""

    async def finish_document(
        self,
        session: SessionState,
        *,
        job_id: str,
        knowledge_id: str,
        course_id: str,
        clazz_id: str,
        jtoken: str,
        dc: str = "",
    ) -> Envelope:
        """完成 PPT / PDF / 文本类任务点。

        :param jtoken: 卡片签发的文档令牌（缺失必然 200 + status=false）
        :param dc: 毫秒时间戳；空则自动生成（真机 App 抓包 2026-11-11）
        :raises EnvelopeFailure: 服务端明确失败，或响应根本不是 JSON
        :returns: 解包后的信封（``msg`` 可区分"首次完成"与"考核点已经完成"）
        """
        response = await self._get(
            session,
            "document_finish",
            headers={
                "X-Requested-With": "XMLHttpRequest",
                # 真机 PPT 查看器页面；服务端会校验 Referer 的模块形态。
                "Referer": f"{DOMAIN_MOOC1_API}/ananas/modules/zt/ppt/index.html?v=122026-1110-2030",
            },
            params={
                "jobid": job_id,
                "knowledgeid": knowledge_id,
                "courseid": course_id,
                "clazzid": clazz_id,
                "jtoken": jtoken,
                # 真机 App 的 PPTX 完成请求额外带这三个字段；缺它们会 200 + 业务失败。
                "checkMicroTopic": "true",
                "microTopicId": "undefined",
                "courseEngineInfo": "false",
                "_dc": dc or str(int(time.time() * 1000)),
            },
        )
        return parse_envelope_strict(response.text).require_ok()

    async def finish_read(
        self,
        session: SessionState,
        *,
        job_id: str,
        knowledge_id: str,
        course_id: str,
        clazz_id: str,
        jtoken: str,
    ) -> Envelope:
        """完成"阅读"类任务点（参数与文档接口一致，作为其失败时的回退通道）。

        :raises EnvelopeFailure: 服务端明确失败，或响应根本不是 JSON
        :returns: 解包后的信封
        """
        response = await self._get(
            session,
            "read_finish",
            params={
                "jobid": job_id,
                "knowledgeid": knowledge_id,
                "courseid": course_id,
                "clazzid": clazz_id,
                "jtoken": jtoken,
            },
        )
        return parse_envelope_strict(response.text).require_ok()

    async def finish_read_v2(
        self,
        session: SessionState,
        *,
        job_id: str,
        knowledge_id: str,
        course_id: str,
        clazz_id: str,
        cpi: str,
        enc: str,
        user_id: str = "null",
    ) -> str:
        """完成「阅读 / 专题阅读」类任务点（**真机实测的真实完成上报**）。

        与上面两个接口的区别：这条**不认 jtoken**，认卡片附件上的 ``enc``
        （阅读器按 ``property.mid`` 从附件里取它，见 ``read/indexV2.html`` 的内联脚本）。
        响应是一张 HTML 页（不是 JSON），**完成与否以平台进度为准** —— 用
        `CourseService.sync_progress` 复核，而不是相信这里的状态码。

        :param enc: 卡片附件顶层或 ``property`` 上的 ``enc``
        :returns: 响应正文（HTML）
        """
        response = await self._get(
            session,
            "readv2_show",
            params={
                "courseid": course_id,
                "knowledgeid": knowledge_id,
                "userid": user_id,
                "ut": "s",
                "clazzId": clazz_id,
                "jobid": "",
                "djobid": job_id,
                "isphone": "false",
                "cpi": cpi,
                "enc": enc,
            },
        )
        return response.text or ""
