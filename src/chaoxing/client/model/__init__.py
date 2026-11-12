"""响应模型包：一接口一模型，全部基于 :class:`~chaoxing.client.model.base.ChaoxingModel`。

模型层的策略（见 ``docs/REFACTOR_PLAN.md`` §5.4）：容忍未知字段、数字字符串互转、
camelCase 与 snake_case 双认、统一用 :class:`~chaoxing.client.model.base.Envelope`
解包 ``status/msg/msg2``，并在解析任何 HTML 之前先过一遍错误页指纹。
"""

from __future__ import annotations

from chaoxing.client.model.base import (
    ERROR_PAGE_MARKERS,
    ChaoxingModel,
    Envelope,
    detect_error_page,
    ensure_not_error_page,
    parse_envelope_strict,
)
from chaoxing.client.model.card import Attachment, CardDefaults, JobCard
from chaoxing.client.model.course import Course
from chaoxing.client.model.video import HeartbeatRequest, HeartbeatVerdict, VideoInfo

__all__ = [
    "ERROR_PAGE_MARKERS",
    "Attachment",
    "CardDefaults",
    "ChaoxingModel",
    "Course",
    "Envelope",
    "HeartbeatRequest",
    "HeartbeatVerdict",
    "JobCard",
    "VideoInfo",
    "detect_error_page",
    "ensure_not_error_page",
    "parse_envelope_strict",
]
