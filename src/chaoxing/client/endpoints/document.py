"""文档 / PPT / 阅读类任务点端点（完成判定不需要真正阅读内容）。"""

from __future__ import annotations

from chaoxing.client.constants import DOMAIN_MOOC1, DOMAIN_MOOC1_API
from chaoxing.client.endpoints.base import Calibration, Endpoint

DOCUMENT_FINISH = Endpoint(
    name="document_finish",
    # 真机 App 抓包（2026-11-11，燃料油 1.1.2 PPTX）：
    # 手机端不是 /ananas/job/document，而是 mooc1-api 的 /mooc-ans/job/document。
    url=f"{DOMAIN_MOOC1_API}/mooc-ans/job/document",
    calibration=Calibration.VERIFIED,
    required_params=("jobid", "knowledgeid", "jtoken"),
    note=(
        "PPT / PDF / 文本类任务点完成。【路径与参数经开源实现交叉验证，待真账号实测】"
        "**jtoken 来自章节卡片，是文档类任务独有令牌**（相当于视频的 dtoken）。"
        "注意：缺 jtoken 时服务端返回 HTTP 200 但正文是 "
        '{"msg":"无效的请求参数","status":false} —— 必须解析正文的 status 字段'
    ),
    params={
        "jobid": "",
        "knowledgeid": "",
        "courseid": "",
        "clazzid": "",
        "jtoken": "",
        "checkMicroTopic": "true",
        "microTopicId": "undefined",
        "courseEngineInfo": "false",
        "_dc": "",
    },
)

READ_FINISH = Endpoint(
    name="read_finish",
    url=f"{DOMAIN_MOOC1}/ananas/job/readv2",
    calibration=Calibration.TEMPLATE,
    required_params=("jobid", "knowledgeid", "jtoken"),
    note="阅读类任务点完成；参数与文档接口一致（作为 document 失败时的回退通道）",
    params={
        "jobid": "",
        "knowledgeid": "",
        "courseid": "",
        "clazzid": "",
        "jtoken": "",
    },
)

CHAPTER_DOCUMENT_MARKS = Endpoint(
    name="chapter_document_marks",
    url=f"{DOMAIN_MOOC1}/mooc-ans/chapter-document/marklists",
    calibration=Calibration.TEMPLATE,
    note="真机录制命中（2026-10-11）。文档 / 阅读任务点的标注列表",
)

READ_V2_SHOW = Endpoint(
    name="readv2_show",
    url=f"{DOMAIN_MOOC1}/mooc-ans/coursedata/readjobv2/show",
    calibration=Calibration.VERIFIED,
    required_params=("courseid", "knowledgeid", "clazzId", "cpi", "djobid", "enc"),
    note=(
        "「阅读 / 专题阅读」任务点的**真实完成上报**（真机录制 + 真账号实测 2026-10-12）。"
        "浏览器打开阅读卡时调的就是它；调用后平台侧进度立刻变成已完成 —— 用 `sync_progress` 复核过"
        "（remote_done 8 → marked 1，「文学与人生」由 failed 变 done）。"
        "参数名照真机抓包：`clazzId`（大写 I）、`isphone=false`、`userid=null` 是字面量。"
        "此前实现走的 /ananas/job/{document,readv2} 对这类卡一律回「无效的请求参数」，"
        "那两个只适用于带 jtoken 的经典文档卡。"
    ),
    params={
        "courseid": "",
        "knowledgeid": "",
        "userid": "null",
        "ut": "s",
        "clazzId": "",
        "jobid": "",
        "djobid": "",
        "isphone": "false",
        "cpi": "",
        "enc": "",
    },
)
