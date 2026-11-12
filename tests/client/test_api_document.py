"""文档 / 阅读任务点：**HTTP 200 ≠ 成功**。

来源：legacy 实测 —— 缺 ``jtoken`` 时服务端返回 200，正文却是
``{"msg":"无效的请求参数","status":false}``；只判状态码会把失败误报成"已完成"，
而任务点并不会因此完成。成功回执形如 ``{"msg":"添加考核点成功","status":true}``。
"""

from __future__ import annotations

import httpx
import pytest
import respx

from chaoxing.client import ChaoxingTransport, DocumentApi, SessionState
from chaoxing.client.exceptions import EnvelopeFailure
from chaoxing.config import ChaoxingSettings

MOOC1 = "https://mooc1.chaoxing.com"
DOC_URL = "https://mooc1-api.chaoxing.com/mooc-ans/job/document"
READ_URL = f"{MOOC1}/ananas/job/readv2"


def _api() -> tuple[ChaoxingTransport, DocumentApi]:
    transport = ChaoxingTransport(ChaoxingSettings(request_min_interval=0, max_retry=0))
    return transport, DocumentApi(transport)


@respx.mock
async def test_finish_document_ok() -> None:
    """成功回执：status=true，msg 可区分首次完成与重复调用。"""
    respx.get(DOC_URL).mock(
        return_value=httpx.Response(200, json={"msg": "添加考核点成功", "status": True})
    )
    transport, api = _api()

    envelope = await api.finish_document(
        SessionState(account_id=1),
        job_id="123",
        knowledge_id="1192366344",
        course_id="265361351",
        clazz_id="151148348",
        jtoken="jt-1",
    )

    assert envelope.ok is True
    assert "考核点" in envelope.reason
    await transport.aclose()


@respx.mock
async def test_finish_document_raises_on_http_200_but_status_false() -> None:
    """★ 核心回归：HTTP 200 + status=false 必须抛错（否则会把失败记成完成）。"""
    respx.get(DOC_URL).mock(
        return_value=httpx.Response(200, json={"msg": "无效的请求参数", "status": False})
    )
    transport, api = _api()

    with pytest.raises(EnvelopeFailure, match="无效的请求参数"):
        await api.finish_document(
            SessionState(account_id=1),
            job_id="123",
            knowledge_id="1192366344",
            course_id="265361351",
            clazz_id="151148348",
            jtoken="",  # 缺 jtoken 正是这条真实事故的触发条件
        )
    await transport.aclose()


@respx.mock
async def test_finish_read_uses_same_judgement() -> None:
    """阅读接口同样以响应体 status 为准（作为文档接口失败时的回退通道）。"""
    respx.get(READ_URL).mock(
        return_value=httpx.Response(200, json={"msg": "考核点已经完成", "status": True})
    )
    transport, api = _api()

    envelope = await api.finish_read(
        SessionState(account_id=1),
        job_id="123",
        knowledge_id="1",
        course_id="2",
        clazz_id="3",
        jtoken="jt-1",
    )

    assert envelope.ok is True
    await transport.aclose()


@respx.mock
async def test_non_json_body_is_treated_as_failure() -> None:
    """正文不是 JSON（例如错误页 HTML）时按失败处理，绝不放过。"""
    respx.get(DOC_URL).mock(return_value=httpx.Response(200, text="<html>错误</html>"))
    transport, api = _api()

    with pytest.raises(EnvelopeFailure):
        await api.finish_document(
            SessionState(account_id=1),
            job_id="1",
            knowledge_id="2",
            course_id="3",
            clazz_id="4",
            jtoken="jt",
        )
    await transport.aclose()
