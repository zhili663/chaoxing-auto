"""任务点卡片解析回归（含两个真实事故）。

样本：``tests/fixtures/job_card_video.json`` —— 来自 legacy 抓包
``cards_1192366344.html`` 里内嵌的 JSON（真实视频任务点，含 otherInfo / attDurationEnc /
videoFaceCaptureEnc 等心跳必需字段）。

两个事故（legacy 上都真实发生过）：

1. ``_jobid`` 只在 ``attachments[].property`` 里 —— 漏摊平会让视频任务点丢掉 jobid，
   心跳发出去服务端永远不认；
2. 同一份课件在 attachments 里出现两次（带令牌的 job 卡片 + 带文件名的附件卡片），
   必须按 jobid 归并，否则同一任务被处理两次。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chaoxing.client.model.card import Attachment
from chaoxing.client.parser.card import (
    extract_cards_payload,
    flatten_property,
    merge_attachments,
    parse_job_card,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "job_card_video.json"


@pytest.fixture(scope="module")
def payload() -> dict[str, Any]:
    """真实卡片 JSON。"""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parses_real_card(payload: dict[str, Any]) -> None:
    """真实样本能解析出带身份的任务点。"""
    card = parse_job_card(payload)

    assert card.attachments, "真实卡片里一个附件都没解析出来"
    primary = card.primary
    assert primary is not None
    assert primary.object_id, "视频任务点必须能取到 objectId"


def test_job_id_recovered_from_nested_property(payload: dict[str, Any]) -> None:
    """事故一：jobid 藏在 property._jobid 里，必须摊平出来。"""
    card = parse_job_card(payload)

    assert card.primary is not None
    assert card.primary.job_id, "嵌套 property 里的 _jobid 没有被摊平"


def test_other_info_is_preserved_verbatim(payload: dict[str, Any]) -> None:
    """``otherInfo`` 的核心段必须原样保留，但**不能带**页面里多挂的 ``&courseId=``。

    实测（2026-10-15）：用户 Edge 真实抓包与工具请求逐字对比 —— 页面 JSON 里
    otherInfo 是 ``…-enc_<hash>&courseId=<id>``，而浏览器发出的心跳里这段
    **到 ``-enc_<hash>`` 为止**（courseId 是独立查询参数）。照页面原样带上它
    必定 403，同一条请求去掉它立刻 200。
    """
    card = parse_job_card(payload)
    assert card.primary is not None

    other_info = card.primary.other_info

    assert other_info.startswith("nodeId_")
    assert "cpi_" in other_info
    assert "-enc_" in other_info
    assert "&courseId=" not in other_info
    assert "&" not in other_info


def test_duration_and_enc_fields_are_parsed(payload: dict[str, Any]) -> None:
    """时长与两个 enc 字段都要能取到（心跳/人脸校验都依赖）。"""
    card = parse_job_card(payload)
    assert card.primary is not None

    assert card.primary.att_duration > 0
    assert card.primary.att_duration_enc
    assert card.primary.face_capture_enc


def test_card_defaults_carry_session_tokens(payload: dict[str, Any]) -> None:
    """``defaults`` 里的令牌（ktoken / defenc / cpi / knowledgeid）是后续请求的必需品。"""
    card = parse_job_card(payload)

    assert card.defaults.ktoken
    assert card.defaults.defenc
    assert card.defaults.cpi
    assert card.defaults.knowledge_id


def test_flatten_property_does_not_override_top_level() -> None:
    """摊平只补缺，不覆盖顶层已有的值（顶层更可信）。"""
    raw = {"objectId": "top", "property": {"objectid": "nested", "type": ".mp4"}}

    flat = flatten_property(raw)

    assert flat["objectId"] == "top"
    assert flat["type"] == ".mp4"


def test_duplicate_attachments_are_merged() -> None:
    """事故二：同一 jobid 的两个附件要合并成一条，字段互补。"""
    items = [
        Attachment(jobid="j1", type="document"),
        Attachment(jobid="j1", name="第一章课件.pdf"),
    ]

    merged = merge_attachments(items)

    assert len(merged) == 1
    assert merged[0].type == "document"
    assert merged[0].name == "第一章课件.pdf"


def test_merge_keeps_first_value_and_order() -> None:
    """归并保留首次出现的顺序与首个非空值。"""
    items = [
        Attachment(jobid="a", name="先出现"),
        Attachment(jobid="b", name="另一个"),
        Attachment(jobid="a", name="后出现"),
    ]

    merged = merge_attachments(items)

    assert [item.job_id for item in merged] == ["a", "b"]
    assert merged[0].name == "先出现"


def test_anonymous_attachments_are_not_merged_together() -> None:
    """没有身份键的附件不能互相吞掉（宁可多处理，不可漏）。"""
    items = [Attachment(type="video"), Attachment(type="document")]

    assert len(merge_attachments(items)) == 2


def test_extract_cards_payload_handles_plain_json() -> None:
    """多数版本直接返回 JSON。"""
    payload = extract_cards_payload('{"attachments": [], "ut": "s"}')

    assert payload == {"attachments": [], "ut": "s"}


def test_extract_cards_payload_handles_embedded_script() -> None:
    """少数版本把 JSON 塞在 script 里，也要能抠出来。"""
    html = '<script>var mArg = {"attachments": [{"objectId": "x"}], "coursename": "某课"};</script>'

    payload = extract_cards_payload(html)

    assert payload is not None
    assert payload["coursename"] == "某课"


@pytest.mark.parametrize("text", ["", "   ", "{broken"])
def test_extract_cards_payload_returns_none_on_garbage(text: str) -> None:
    """完全不像卡片响应时才返回 None，让上层显式处理（而不是抛异常炸掉整条链路）。"""
    assert extract_cards_payload(text) is None


def test_extract_cards_payload_empty_chapter_is_not_a_failure() -> None:
    """**空章节是正常状态**，必须返回合法的空卡片而不是 None。

    学习通里有些节点只能看、没有任务点，响应是一段没有任何卡片元素的 HTML。
    早先把这种情况也判为"解析失败"，后果是扫描一门课刷出几十条「卡片无法解析」警告
    —— 真出问题时反而看不见告警了（噪音淹没信号）。
    """
    payload = extract_cards_payload("<html><body>这一节只有讨论，没有任务点</body></html>")

    assert payload is not None
    assert payload["attachments"] == []
    assert isinstance(payload["defaults"], dict)


def test_extract_cards_from_html_reads_real_card_shape() -> None:
    """真实线上形态：``<iframe class="ans-...-retract" data="{...}">``。

    这是 ``knowledge/cards`` **实际返回的格式**（HTML 片段，JSON 被实体转义），
    而不是 JSON —— mock 测试当年用的是自己编的 JSON，所以这条路径一直没被走到，
    真账号扫任务点时表现为"响应有 9KB、就是解析不出来"。
    """
    from chaoxing.client.parser.card import extract_cards_from_html

    html = (
        '<div class="ans-cc">'
        '<iframe class="ans-module ans-insertvideo-retract ans-attach-online" '
        'data="{&quot;objectid&quot;:&quot;abc123&quot;,&quot;name&quot;:&quot;1.1 导论.mp4&quot;,'
        '&quot;type&quot;:&quot;.mp4&quot;,&quot;_jobid&quot;:&quot;job999&quot;}"></iframe>'
        '<iframe class="ans-module ans-insertdoc-retract ans-attach-online" '
        'data="{&quot;objectid&quot;:&quot;def456&quot;,&quot;name&quot;:&quot;讲义.pptx&quot;,'
        '&quot;type&quot;:&quot;.pptx&quot;,&quot;_jobid&quot;:&quot;job888&quot;}"></iframe>'
        "</div>"
    )

    payload = extract_cards_from_html(html)

    assert payload is not None
    attachments = payload["attachments"]
    assert [item["type"] for item in attachments] == ["video", "document"]
    assert attachments[0]["objectId"] == "abc123"
    assert attachments[0]["jobId"] == "job999"

    card = parse_job_card(payload)
    assert [item.kind for item in card.attachments] == ["video", "document"]
    # 摊平后 _jobid 必须到顶层 —— 心跳全靠它
    assert card.attachments[0].identity == "job999"


def test_defaults_ktoken_is_extracted_from_html() -> None:
    """``defaults`` 里的 ``ktoken`` 必须提出来 —— 换票与提交都要用。

    这条是真账号踩出来的：HTML 路径原先只提 ``attachments``，
    于是 `ktoken` 一路上都是空值。而真机实测里**所有卡片都带 ktoken**（32 位十六进制），
    对照历史抓包一眼就能看出差别。
    """
    from chaoxing.client.parser.card import extract_cards_from_html, parse_job_card

    html = (
        "<html><script>"
        'var cfg = {"coursename":"某课","defaults":{"fid":"3568",'
        '"ktoken":"028333f71f350ae79b3fabbc0a299d00","silentFaceCapture":0}};'
        "</script>"
        '<div class="ans-module" data="{&quot;objectid&quot;:&quot;abc&quot;,'
        '&quot;_jobid&quot;:&quot;job1&quot;}"></div>'
        "</html>"
    )

    payload = extract_cards_from_html(html)

    assert payload is not None
    assert payload["defaults"]["ktoken"] == "028333f71f350ae79b3fabbc0a299d00"
    card = parse_job_card(payload)
    # 模型侧也要拿到（heartbeat / 换票读的都是 card.defaults.ktoken）
    assert card.defaults.ktoken == "028333f71f350ae79b3fabbc0a299d00"
    assert card.defaults.fid == "3568"


def test_defaults_is_empty_when_absent() -> None:
    """没有 ``defaults`` 时返回空字典，而不是抛异常。"""
    from chaoxing.client.parser.card import extract_defaults_from_html

    assert extract_defaults_from_html("<html>什么也没有</html>") == {}


def test_other_info_is_extracted_from_page_not_attachment() -> None:
    """``otherInfo`` 只在**页面 JSON** 里，不在附件元素上 —— 心跳必需。

    实测：新版卡片的附件元素（``data="{...}"``）只有 ``objectid / name / type /
    size / mid / _jobid / jobid / doublespeed / ywbf``，**没有** ``otherInfo``。
    而心跳 URL 的 ``otherInfo`` 是必填的：

        nodeId_<chapterId>-cpi_<cpi>-rt_d-ds_1-ff_d-be_0_0-vt_1-v_6-enc_<hash>&courseId=<id>

    漏提取的表现是"心跳参数里 otherInfo 为空"——服务端直接 403，
    而 403 页面不给任何原因，只有逐参数比对才发现。
    """
    from chaoxing.client.parser.card import extract_cards_from_html, extract_other_info

    html = (
        '<html><script>var cfg = {"defaults":{"ktoken":"abc"},'
        '"otherInfo":"nodeId_1214035150-cpi_581371075-rt_d-ds_1-ff_d-be_0_0-vt_1-v_6-enc_b5939216739074f104c29039d6fe4660&courseId=265918108"};'
        "</script>"
        '<div class="ans-module" data="{&quot;objectid&quot;:&quot;986cd91019d0b3ee878ced0a071dd833&quot;,'
        '&quot;_jobid&quot;:&quot;164905573148985&quot;}"></div></html>'
    )

    other = extract_other_info(html)
    assert other.startswith("nodeId_1214035150-cpi_581371075")

    payload = extract_cards_from_html(html)
    assert payload is not None
    card = parse_job_card(payload)
    # 附件本身没有这个字段，靠注入拿到 —— 下游读的正是附件字段
    assert card.attachments[0].other_info == other


def test_other_info_is_empty_when_absent() -> None:
    """页面里没有 otherInfo 时返回空串（老版卡片），而不是抛异常。"""
    from chaoxing.client.parser.card import extract_other_info

    assert extract_other_info("<html>没有它</html>") == ""


def test_extract_cards_from_html_ignores_unrelated_data_attributes() -> None:
    """页面上无关的 ``data-*`` 不该被当成任务点。"""
    from chaoxing.client.parser.card import extract_cards_from_html

    html = (
        '<div data="{"tracker":"analytics-id-1234567890"}"></div>'
        '<span data="{"tooltip":"只是提示文案"}"></span>'
    )

    payload = extract_cards_from_html(html)
    assert payload is not None
    assert payload["attachments"] == []


def test_quiz_task_point_is_recognized_by_workid() -> None:
    """**测验类任务点没有 objectid / jobid**，身份键是 ``workid``。

    少了这一条，整类测验任务点会被当成"页面上无关的 data 属性"丢掉 ——
    表现是扫完一门课一个测验都没有，而线上截图里明明有「1.7 章节测验」。
    """
    from chaoxing.client.parser.card import extract_cards_from_html, parse_job_card

    html = (
        '<div class="ans-cc">'
        '<div class="ans-module ans-attach-online" '
        'data="{&quot;jtoken&quot;:&quot;tok123&quot;,&quot;property&quot;:'
        '{&quot;workid&quot;:&quot;2384727764&quot;},&quot;jobid&quot;:&quot;job777&quot;}"></div>'
        "</div>"
    )

    payload = extract_cards_from_html(html)

    assert payload is not None
    assert len(payload["attachments"]) == 1
    card = parse_job_card(payload)
    # 身份键藏在 property 里 —— 只看顶层同样会漏掉
    assert card.attachments[0].kind == "quiz"
    assert card.attachments[0].job_id == "job777"


def test_cards_payload_merges_jtoken_from_embedded_script() -> None:
    """HTML 卡没有 ``jtoken`` 时，从内嵌脚本的 JSON 里补上（2026-10-12 真账号实测）。

    同一份响应里两份载荷**各有缺失**：HTML 卡带类型信号（``_jobid`` / 扩展名 / workid），
    内嵌脚本（``mArg = {…}``）带 ``jtoken``。此前只取 HTML 版，于是文档任务点带着空
    ``jtoken`` 落库 —— 真机表现为 4/4 文档点「缺 jtoken」必败。
    """
    text = (
        '<div class="card" data="{&quot;objectid&quot;:&quot;&quot;'
        ',&quot;_jobid&quot;:&quot;read-212697622&quot;,&quot;title&quot;:&quot;文学与人生&quot;}"></div>'
        '<script>mArg = {"hiddenConfig":false,"attachments":'
        '[{"begins":0,"jtoken":"d1bb1668cbe4028a7dd0c7dfcc6eed8a",'
        '"property":{"_jobid":"read-212697622","id":"212697622"}}]};</script>'
    )

    payload = extract_cards_payload(text)

    assert payload is not None
    attachment = payload["attachments"][0]
    assert attachment["jtoken"] == "d1bb1668cbe4028a7dd0c7dfcc6eed8a"
    # 类型信号来自 HTML 版，不能被脚本版覆盖
    assert attachment["jobId"] == "read-212697622"
    card = parse_job_card(payload)
    assert card.attachments[0].jtoken == "d1bb1668cbe4028a7dd0c7dfcc6eed8a"


def test_attachment_name_falls_back_to_property_title() -> None:
    """卡片把标签放在 ``property.title``，别名要收下它（2026-10-12 真机实测）。

    落库取的是 ``attachment.name``（``_store_points`` 的 ``title=attachment.name``），
    只认 ``name``/``filename`` 时「阅读/文档」类任务点的标题会一路落成空串 ——
    排障时只能看到一串 id。
    """
    text = (
        '<div class="card" data="{&quot;objectid&quot;:&quot;&quot;'
        ',&quot;_jobid&quot;:&quot;read-9&quot;,&quot;title&quot;:&quot;文学与人生&quot;}"></div>'
    )

    card = parse_job_card(extract_cards_payload(text) or {})

    assert card.attachments[0].name == "文学与人生"
