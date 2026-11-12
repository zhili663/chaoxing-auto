"""响应模型基类、状态信封与错误页指纹。

学习通响应有三个必须处理的特征（都是真实踩过的坑）：

1. **字段名不稳定**：同一语义在不同子系统里叫 ``objectId`` / ``objectid`` / ``object_id``；
2. **HTTP 200 ≠ 成功**：缺参时返回 200，正文却是
   ``{"status": false, "msg": "无效的请求参数"}`` —— 只判状态码会把失败当成功；
3. **错误页伪装成正常页**：题目页会返回 3004 权限错误页（同样 HTTP 200），
   不识别就会被兜底解析器当成题目，**把页脚文字当成一道题提交出去**。
"""

from __future__ import annotations

import json
from typing import Any, Self

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from chaoxing.client.exceptions import EnvelopeFailure, ErrorPageDetected


class ChaoxingModel(BaseModel):
    """容错解析基类。

    * 未知字段忽略（学习通随时加字段，不能因此解析崩）；
    * 数字与字符串互转（``"3"`` / ``3`` 都能进 ``str`` 字段，反之亦然）；
    * 首尾空白自动裁剪；
    * **camelCase 与 snake_case 都认**（同一语义在不同子系统里叫 ``objectId`` /
      ``object_id``，靠 ``alias_generator`` + ``populate_by_name`` 同时兼容）。
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
        str_strip_whitespace=True,
        coerce_numbers_to_str=True,
    )

    @classmethod
    def from_payload(cls, raw: dict[str, Any]) -> Self:
        """由原始 dict 构造（统一的入口，方便将来插入清洗逻辑）。

        :param raw: 接口返回的原始字典
        :returns: 模型实例
        """
        return cls.model_validate(raw)


class Envelope(ChaoxingModel):
    """通用响应信封：``{"status": bool, "msg": str, "msg2": str}``。

    ``status`` 缺失时不武断判失败 —— 有些接口（如文档完成）只在失败时给 msg。
    """

    status: bool | None = None
    msg: str = ""
    msg2: str = ""
    message: str = ""

    @property
    def ok(self) -> bool:
        """业务是否成功（``status`` 为 False 才算失败）。"""
        return self.status is not False

    @property
    def reason(self) -> str:
        """人类可读的原因（**错误信息在 msg2**，实测如此）。"""
        return self.msg2 or self.msg or self.message

    def require_ok(self) -> Self:
        """业务失败时抛 :class:`EnvelopeFailure`。

        :raises EnvelopeFailure: ``status=false``
        :returns: 自身（便于链式调用）
        """
        if not self.ok:
            raise EnvelopeFailure(self.reason or "服务端返回失败状态", payload=self.model_dump())
        return self


#: 错误页指纹 -> 人读说明（命中即中止解析，绝不当成正常内容）
ERROR_PAGE_MARKERS: tuple[tuple[str, str], ...] = (
    ("3004", "会话或权限错误：长时间未操作，或没有该页面访问权限"),
    ("温馨提示", "学习通错误提示页"),
    ("enc校验失败", "enc 签名校验失败（通常缺 stuenc / t 参数）"),
    ("没有权限", "无权限访问该页面"),
    ("课程不存在", "课程不存在或已被移除"),
    ("您没有访问该课程的权限", "无该课程访问权限"),
)


def detect_error_page(text: str) -> str | None:
    """在响应正文里找错误页指纹。

    :param text: 已解码的响应正文
    :returns: 命中的标记文本；未命中返回 ``None``
    """
    if not text:
        return None
    for marker, _ in ERROR_PAGE_MARKERS:
        if marker in text:
            return marker
    return None


def ensure_not_error_page(text: str) -> None:
    """命中错误页指纹就抛异常。

    :param text: 已解码的响应正文
    :raises ErrorPageDetected: 正文被判定为错误页
    """
    marker = detect_error_page(text)
    if marker is not None:
        reason = dict(ERROR_PAGE_MARKERS).get(marker, "未知错误页")
        raise ErrorPageDetected(
            f"命中学习通错误页指纹「{marker}」：{reason}",
            marker=marker,
            payload={"marker": marker, "body": (text or "")[:2000]},
        )


def parse_envelope_strict(text: str) -> Envelope:
    """解析"**必须是 JSON**"的接口响应（文档完成 / 测验提交这类）。

    这些接口要么给 JSON，要么就是错误页 HTML —— **非 JSON 一律按失败处理**。
    legacy 的教训：错误页同样返回 HTTP 200，若按"没有 status 就算成功"处理，
    就会把"拿到错误页"记成"任务点已完成"。

    :param text: 响应正文
    :raises EnvelopeFailure: 正文不是 JSON 对象
    :returns: 解包后的 :class:`Envelope`
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        raise EnvelopeFailure(
            f"响应不是 JSON（可能是错误页）：{(text or '')[:120]}",
            payload={"raw": (text or "")[:2000]},
        ) from None
    if not isinstance(data, dict):
        raise EnvelopeFailure("响应 JSON 不是对象")
    return Envelope.from_payload(data)
