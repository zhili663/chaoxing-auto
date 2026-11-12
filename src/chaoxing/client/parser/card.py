"""任务点卡片解析（含两个真实事故的修复）。

事故一：``_jobid`` 藏在 ``attachments[].property`` 里
    卡片顶层没有 jobid，legacy 因此丢过整个视频任务点的 jobid，
    表现为"心跳发出去了，服务端永远不认"。

事故二：同一份课件在 attachments 里出现两次
    job 卡片带令牌、附件卡片带文件名与页数，``jobid`` 相同；
    必须按身份键归并，否则同一任务被处理两次。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from html import unescape
from typing import Any

from chaoxing.client.model.card import Attachment, JobCard
from chaoxing.client.parser.common import extract_json_object

log = logging.getLogger(__name__)

#: 卡片 JSON 在页面源码里的可能起点
_PAYLOAD_ANCHORS = ('{"hiddenConfig"', '{"attachments"', '{"coursename"')

#: HTML 卡片元素：``<iframe ... data="{...}">``（JSON 被 HTML 实体转义）
_CARD_DATA_PATTERN = re.compile(
    r"<(\w+)\b([^>]*?)\bdata=\"([^\"]{20,})\"", re.IGNORECASE | re.DOTALL
)

#: 判定"这是个任务点"的身份键（四种里至少有一个）。
#: ``workid`` 是**测验 / 作业类**任务点的身份键 —— 它们既没有 objectid 也没有 jobid，
#: 少了它整类任务点都会被当成"页面上无关的 data 属性"丢掉。
_IDENTITY_KEYS = frozenset({"objectid", "_jobid", "jobid", "workid"})

#: 元素特征 → 任务点类型（命中第一个即返回）
_KIND_HINTS: tuple[tuple[str, str], ...] = (
    ("insertvideo", "video"),
    ("insertdoc", "document"),
    ("insertwork", "quiz"),
    ("inserttask", "quiz"),
    ("inserthtml", "document"),
)


def _guess_kind(element: str, attrs: str, payload: Mapping[str, Any]) -> str:
    """猜任务点类型（视频 / 文档 / 测验）。

    先看元素特征（class 里的 ``insertvideo`` 之类），再看 **payload 里的身份字段** ——
    后者才是可靠信号：带 ``workid`` 的就是测验（作业 / 章节测验都会带它），
    而这类卡片的 class 上常常什么关键词都没有，只看 class 会一路判成 ``unknown``。

    :param element: 元素名（iframe / div ...）
    :param attrs: 元素的其它属性文本
    :param payload: 元素上的 JSON
    :returns: ``video`` / ``document`` / ``quiz`` 之一
    """
    haystack = f"{element} {attrs}".lower()
    for hint, kind in _KIND_HINTS:
        if hint in haystack:
            return kind

    # ⚠️ 身份字段常藏在 ``property`` 子字典里（``workid``、``jtoken`` 都是），
    # 只看顶层会一路判成 unknown —— 而 unknown 的任务点会被刷课流程跳过。
    nested = payload.get("property")
    probe: dict[str, Any] = dict(payload)
    if isinstance(nested, Mapping):
        for key, value in nested.items():
            probe.setdefault(str(key), value)

    if probe.get("workid") or probe.get("workId"):
        return "quiz"
    if probe.get("jtoken") and not probe.get("objectid"):
        return "document"

    extension = str(probe.get("type") or "").lower()
    if extension in {".mp4", ".flv", ".m3u8"}:
        return "video"
    return "document"


def flatten_property(raw: Mapping[str, Any]) -> dict[str, Any]:
    """把 ``property`` 里的键提升到附件顶层（**不覆盖**已有键）。

    :param raw: 单个 attachment 的原始字典
    :returns: 摊平后的新字典（原字典不被修改）
    """
    flat: dict[str, Any] = dict(raw)
    nested = raw.get("property")
    if isinstance(nested, Mapping):
        for key, value in nested.items():
            flat.setdefault(str(key), value)
    return flat


def _merge_two(base: Attachment, extra: Attachment) -> Attachment:
    """把 ``extra`` 的非空字段补进 ``base``（base 已有的值优先保留）。"""
    empty = ("", 0.0, 0, False, None)
    updates: dict[str, Any] = {}
    for name in Attachment.model_fields:
        current = getattr(base, name)
        candidate = getattr(extra, name)
        if current in empty and candidate not in empty:
            updates[name] = candidate
    return base.model_copy(update=updates) if updates else base


def merge_attachments(items: Sequence[Attachment]) -> list[Attachment]:
    """按身份键（jobid，退化到 objectId）归并重复附件。

    :param items: 原始附件序列
    :returns: 归并后的附件列表（保持首次出现的顺序；无身份键的项原样保留）
    """
    merged: dict[str, Attachment] = {}
    order: list[str] = []

    for index, item in enumerate(items):
        key = item.identity or f"__anonymous_{index}"
        existing = merged.get(key)
        if existing is None:
            merged[key] = item
            order.append(key)
        else:
            merged[key] = _merge_two(existing, item)

    return [merged[key] for key in order]


def parse_job_card(raw: Mapping[str, Any]) -> JobCard:
    """把卡片原始 JSON 解析成模型（附件先摊平 ``property``，再归并重复项）。

    :param raw: 卡片 JSON（``extract_cards_payload`` 的产物）
    :returns: :class:`~chaoxing.client.model.card.JobCard`
    """
    data: dict[str, Any] = dict(raw)
    attachments = data.get("attachments")
    if isinstance(attachments, list):
        flattened = [flatten_property(item) for item in attachments if isinstance(item, Mapping)]
        card = JobCard.model_validate({**data, "attachments": flattened})
        card.attachments = merge_attachments(card.attachments)
        return card
    return JobCard.model_validate(data)


def extract_cards_payload(text: str) -> dict[str, Any] | None:
    """从卡片接口响应里取出 JSON 主体。

    三条路依次走（**顺序是实测调出来的**）：

    1. 整段就是 JSON —— 少数版本的接口直接给 JSON；
    2. **HTML 卡片**（``<iframe ... data="{...}">``）—— 当前线上就是这个形态，
       而且它携带**类型信息**（``workid`` / ``jtoken`` / 扩展名），判定最准；
    3. 内嵌脚本里的 JSON —— 老版本兜底。

    ⚠️ 第 2 步必须排在第 3 步**前面**：内嵌 JSON 里也有 ``attachments``，
    但它**不带类型信号**，走那条路会让测验、文档一律落成 ``unknown`` 而被刷课流程跳过 ——
    实测同一份响应：走 HTML 解析得到 ``quiz``，走内嵌 JSON 得到 ``unknown``。

    :param text: 响应正文
    :returns: 与 JSON 版**结构等价**的 payload（下游解析无需区分来源）；都无法解析时返回 ``None``
    """
    stripped = (text or "").strip()
    if not stripped:
        return None

    try:
        loaded = json.loads(stripped)
    except json.JSONDecodeError:
        loaded = None
    if isinstance(loaded, dict):
        return loaded

    html_payload = extract_cards_from_html(stripped)
    if html_payload and html_payload.get("attachments"):
        # HTML 卡片带**类型信号**（workid / 扩展名 / jtoken 之外的判定依据），所以它是基底；
        # 但实测（2026-10-12，真账号 `read-212697632` 类阅读卡）HTML 版**没有 `jtoken`**，
        # 而同一份响应里内嵌脚本的 JSON（`mArg = {…}`）**有** —— 不互补就会让文档任务点
        # 带着空 jtoken 落库，执行时必然失败。这里按身份键把内嵌版独有的字段补齐。
        script_payload = _script_payload(stripped)
        if script_payload:
            _merge_attachments(html_payload, script_payload)
        return html_payload

    for anchor in _PAYLOAD_ANCHORS:
        blob = extract_json_object(stripped, anchor)
        if blob is None:
            continue
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError:
            log.debug("锚点 %s 处的 JSON 解析失败", anchor)
            continue
        if isinstance(parsed, dict):
            return parsed

    # 走到这里说明没有可识别的任务点：是空章节就给合法空卡片，否则才是真失败
    return html_payload


def _script_payload(text: str) -> dict[str, Any] | None:
    """取内嵌脚本里的卡片 JSON（按 :data:`_PAYLOAD_ANCHORS` 依次尝试）。

    :param text: 响应正文
    :returns: 带 ``attachments`` 的 dict；找不到时 ``None``
    """
    for anchor in _PAYLOAD_ANCHORS:
        blob = extract_json_object(text, anchor)
        if blob is None:
            continue
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError:
            log.debug("锚点 %s 处的 JSON 解析失败", anchor)
            continue
        if isinstance(parsed, dict) and parsed.get("attachments"):
            return parsed
    return None


def _attachment_keys(item: Mapping[str, Any]) -> set[str]:
    """一个附件可用于互相匹配的身份键（顶层与 ``property`` 里都看）。

    :param item: 附件字典（HTML 版或脚本版）
    :returns: 非空身份键集合
    """
    keys: set[str] = set()
    for name in ("jobId", "jobid", "_jobid", "objectId", "objectid"):
        value = item.get(name)
        if value:
            keys.add(str(value))
    prop = item.get("property")
    if isinstance(prop, Mapping):
        for name in ("id", "_jobid", "jobid", "mid", "objectid"):
            value = prop.get(name)
            if value:
                keys.add(str(value))
    return keys


def _merge_attachments(base: dict[str, Any], extra: dict[str, Any]) -> None:
    """把 ``extra`` 里附件独有的字段补进 ``base``（**就地修改** ``base``）。

    只补 ``base`` 缺失或为空的字段：基底（HTML 卡）的类型判定优先，绝不被覆盖。
    这两份载荷测的是同一批任务点，只是**字段各有缺失** —— HTML 版带类型信号、
    脚本版带 ``jtoken``。

    :param base: HTML 版载荷（会被修改）
    :param extra: 脚本版载荷（只读）
    """
    pairs = [
        (_attachment_keys(item), item)
        for item in (extra.get("attachments") or [])
        if isinstance(item, Mapping)
    ]
    if not pairs:
        return
    for item in base.get("attachments") or []:
        if not isinstance(item, dict):
            continue
        keys = _attachment_keys(item)
        if not keys:
            continue
        for other_keys, other in pairs:
            if not keys & other_keys:
                continue
            for name, value in other.items():
                if name == "property" or value in ("", None):
                    continue
                if item.get(name) in ("", None):
                    item[name] = value
            base_prop = item.get("property")
            other_prop = other.get("property")
            if isinstance(base_prop, dict) and isinstance(other_prop, Mapping):
                for name, value in other_prop.items():
                    if value in ("", None):
                        continue
                    if base_prop.get(name) in ("", None):
                        base_prop[name] = value
            break


#: 页面里 ``otherInfo`` 的字面量形态（心跳必需的参数串）
_OTHER_INFO_PATTERN = re.compile(
    r'"(?:otherInfo|otherinfo)"\s*:\s*"([^"]{20,600}?)"',
    re.IGNORECASE,
)

#: 页面级（**不在附件元素上**）的其余心跳必需字段
_PAGE_LEVEL_FIELDS: tuple[str, ...] = ("attDuration", "attDurationEnc")


def extract_page_level_fields(text: str) -> dict[str, str]:
    """从卡片页里取出**不在附件元素上**的心跳必需字段。

    实测（拿真浏览器抓到的真实心跳逐参数对比）：心跳要带 ``attDuration`` 与
    ``attDurationEnc``，而这两个值**不在附件元素**（``data="{...}"``）里 ——
    附件元素只有 ``objectid / name / type / size / hsize / mid / _jobid / jobid /
    doublespeed / ywbf``。它们和 ``otherInfo`` 一样写在**页面 JSON** 里：

        "attDuration":"441","attDurationEnc":"38abb0e752333cfbce1e913648db8825"

    漏了它们的表现是心跳 URL 上这两个参数为空 → 服务端 403，且 403 页面不给原因。

    :param text: 卡片页 HTML
    :returns: ``{字段名: 值}``，只含找到的字段
    """
    found: dict[str, str] = {}
    for name in _PAGE_LEVEL_FIELDS:
        match = re.search(rf'"{name}"\s*:\s*"?([^",}}]*)"?', text or "")
        if match and match.group(1):
            found[name] = match.group(1)
    return found


def extract_other_info(text: str) -> str:
    """从卡片页里取出 ``otherInfo``（心跳必需，**不在附件元素上**）。

    为什么单独提它：附件元素（``data="{...}"``）上**没有** ``otherInfo`` ——
    实测新版卡片里它的键只有 ``objectid / name / type / size / mid / _jobid /
    jobid / doublespeed / ywbf``。而心跳 URL 的 ``otherInfo`` 是必需的：

        nodeId_<chapterId>-cpi_<cpi>-rt_d-ds_1-ff_d-be_0_0-vt_1-v_6-enc_<hash>

    ★ **不能带页面里多挂的 ``&courseId=<id>`` 段。** 实测（2026-10-15，用户 Edge
    抓包与工具请求逐字对比）：页面 JSON 里的 otherInfo 是
    ``…-enc_<hash>&courseId=<id>``，而浏览器真正发出的 heartbeat 里 otherInfo
    **到 ``-enc_<hash>`` 为止**，``courseId`` 是**独立的查询参数**。
    照页面原样带上 ``&courseId=`` 必定 403（同一条请求去掉它立刻 200）。

    它写在**页面的 JSON 数据里**（章节级，同一章节的任务点共用），
    所以要从整页文本里找，而不是从附件元素上取。

    :param text: 卡片页 HTML
    :returns: ``otherInfo`` 字符串（已截掉 ``&courseId=`` 尾巴）；没有则返回空串
    """
    match = _OTHER_INFO_PATTERN.search(text or "")
    if match is None:
        return ""
    from html import unescape as _unescape

    # 页面里可能写成 ``&amp;courseId=``，所以先 unescape 再截断。
    return _unescape(match.group(1)).split("&", 1)[0].strip()


def extract_defaults_from_html(text: str) -> dict[str, Any]:
    """从 HTML 里取出卡片的 ``defaults`` 对象（会话级参数）。

    为什么单独提它：``defaults`` 里有 **``ktoken``**、``fid``、``mtEnc`` 等会话级参数，
    测验换票与提交都要用。HTML 路径原先只提 ``attachments``，于是这些字段全空 ——
    表现是"卡片扫到了、但换票时 `ktoken=` 是空值"，而真账号实测里
    所有卡片都带 ktoken（32 位十六进制串）。

    :param text: HTML 正文
    :returns: ``defaults`` 字典；没有则返回空字典
    """
    marker = '"defaults"'
    index = text.find(marker)
    if index < 0:
        return {}
    brace = text.find("{", index + len(marker))
    if brace < 0:
        return {}
    try:
        parsed, _ = json.JSONDecoder().raw_decode(text, brace)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def extract_cards_from_html(text: str) -> dict[str, Any] | None:
    """把 HTML 卡片片段转成与 JSON 版等价的 payload。

    不依赖 class 名（各课程类型的前缀不同、顺序也不保证），只认一件事：
    **带 ``data="{...}"`` 且 JSON 里有任务点身份键**的元素。
    这样既覆盖视频 / 文档 / 测验，也不会把页面上无关的 ``data-*`` 误当任务点。

    :param text: HTML 正文
    :returns: ``{"attachments": [...]}``；没有识别到任务点时返回 ``None``
    """
    attachments: list[dict[str, Any]] = []
    for match in _CARD_DATA_PATTERN.finditer(text):
        element, attrs, raw = match.group(1), match.group(2), match.group(3)
        try:
            payload = json.loads(unescape(raw))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        # 身份键可能在顶层，也可能在 ``property`` 里 —— 两边都看一眼
        nested = payload.get("property")
        probe = dict(payload)
        if isinstance(nested, Mapping):
            for key, value in nested.items():
                probe.setdefault(str(key), value)
        if not _IDENTITY_KEYS.intersection(probe):
            continue
        attachments.append(
            {
                "type": _guess_kind(element, attrs, payload),
                "objectId": str(payload.get("objectid") or ""),
                "jobId": str(payload.get("_jobid") or payload.get("jobid") or ""),
                # 原样塞进 property：下游 flatten_property 会把这些键摊平到顶层
                "property": payload,
            }
        )

    if not attachments:
        # 空章节是**正常状态**（学习通里有些节点只能看、没有任务点），
        # 所以这里返回一个合法的空卡片而不是 None —— 后者会让上层误报"解析失败"，
        # 真出问题时的告警就会被淹没在这片噪音里。
        if "<" not in text:
            return None
        return {
            "attachments": [],
            "defaults": extract_defaults_from_html(text),
            "otherInfo": extract_other_info(text),
        }

    # ★ 必须带上 defaults / otherInfo / 页面级字段：
    #   defaults 里有 ktoken / fid 等会话级参数（换票与提交要用），
    #   otherInfo 与 attDuration / attDurationEnc 是心跳的必需参数 ——
    #   它们**都不在附件元素上**，只在页面 JSON 里，所以要在这里注入到每个附件
    #   （下游 `_store_points` 读的是附件字段）。
    other_info = extract_other_info(text)
    page_fields = extract_page_level_fields(text)
    for item in attachments:
        if other_info and not item.get("otherInfo"):
            item["otherInfo"] = other_info
        for name, value in page_fields.items():
            item.setdefault(name, value)
    return {
        "attachments": attachments,
        "defaults": extract_defaults_from_html(text),
        "otherInfo": other_info,
        **page_fields,
    }
