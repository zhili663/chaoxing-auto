"""视频相关的响应模型与心跳回执判定。

回执判定（``HeartbeatVerdict``）值得单独建模的原因：学习通不同课程的心跳回执
格式并不统一（JSON / 纯文本 / 空体 / HTML 提示页），**判定错了就会谎报成功**——
legacy 明确要求"宁可重试，也不谎报"。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import AliasChoices, Field

from chaoxing.client.model.base import ChaoxingModel

#: 人脸抓拍相关标记（命中即需要人工介入，继续推进只会拿到 403）
FACE_MARKERS = ("videoFaceCapture", "faceCapture", "人脸", "活体")

#: 端点切换标记（主通道不可用时改走备用域名）
SWITCH_MARKERS = ("params error", "参数错误", "not found")

#: 文本形式的完成标记（少数课程的心跳回执是纯文本）
FINISHED_MARKERS = ("已完成", "该任务点已完成", "finished")


class VideoInfo(ChaoxingModel):
    """``/ananas/status/{objectId}`` 的响应（拿 dtoken 与真实时长）。"""

    status: str = ""
    dtoken: str = ""
    duration: float = 0.0
    object_id: str = Field(default="", validation_alias=AliasChoices("objectId", "objectid"))
    #: 部分课程回传的人脸校验标记
    video_face_capture_enc: str = Field(
        default="",
        validation_alias=AliasChoices(
            "videoFaceCaptureEnc",
        ),
    )

    @property
    def usable(self) -> bool:
        """是否拿到了可用令牌（没有 dtoken 就无法上报进度）。"""
        return bool(self.dtoken)


@dataclass(slots=True)
class HeartbeatVerdict:
    """一次心跳回执的解读结果。

    :param passed: 服务端已认可该任务点完成
    :param throttled: 被频控（实测心跳会持续返回 202 且 ``isPassed`` 恒 false）
    :param forbidden: 参数或签名被拒（403：常见于 rt 非页面值、enc/dtoken 过期）
    :param face_required: 需要人脸抓拍
    :param switch_endpoint: 主通道不可用，应切备用域名
    :param status_code: HTTP 状态码
    :param raw: 回执原文（截断，便于排障）
    """

    passed: bool = False
    throttled: bool = False
    forbidden: bool = False
    face_required: bool = False
    switch_endpoint: bool = False
    status_code: int = 0
    raw: str = ""

    @property
    def needs_human(self) -> bool:
        """是否需要人工介入（人脸抓拍）。"""
        return self.face_required

    @property
    def retryable(self) -> bool:
        """是否值得退避后重试（频控 / 端点切换）。"""
        return self.throttled or self.switch_endpoint

    @property
    def ok(self) -> bool:
        """是否"服务端正常收下且未报错"（不表示任务点已完成）。"""
        return not (self.forbidden or self.throttled or self.face_required or self.switch_endpoint)

    def as_detail(self) -> dict[str, object]:
        """转成可日志/可审计的字典。"""
        return {
            "passed": self.passed,
            "throttled": self.throttled,
            "forbidden": self.forbidden,
            "face_required": self.face_required,
            "switch_endpoint": self.switch_endpoint,
            "status_code": self.status_code,
        }


@dataclass(slots=True)
class HeartbeatRequest:
    """一次心跳要用到的全部字段（由卡片 + 会话拼装）。"""

    clazz_id: str
    user_id: str
    job_id: str
    object_id: str
    course_id: str
    other_info: str
    playing_time: float
    duration: float
    rate: str = "2.0"
    dtype: str = "Video"
    cpi: str = ""
    dtoken: str = ""
    extra: dict[str, str] = field(default_factory=dict)


#: 心跳必需的卡片字段（缺一个就可能 403；来源：真机逐参数对照，见 docs/MOOC_ANS_403.md）
HEARTBEAT_CARD_FIELDS: tuple[str, ...] = (
    "clazzId",
    "courseId",
    "userid",
    "otherInfo",
    "cpi",
    "attDuration",
    "attDurationEnc",
)


def heartbeat_prerequisites(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """返回卡片 payload 里**缺失**的心跳必需字段（空 tuple = 齐全）。

    :param payload: 任务点透传的卡片字段
    :returns: 缺失字段名（按 :data:`HEARTBEAT_CARD_FIELDS` 顺序）
    """
    return tuple(name for name in HEARTBEAT_CARD_FIELDS if not payload.get(name))


@dataclass(slots=True)
class ActivationProbe:
    """dtoken 激活探测：打开播放器页前后各取一次 ``/ananas/status`` 的 dtoken。

    真机对照（``docs/MOOC_ANS_403.md`` §五）：**从未被真实播放过**的视频，两次 dtoken 相同；
    曾被播放过的，打开播放器页后 dtoken 会换成一个可用于心跳的值。
    """

    object_id: str
    dtoken_before: str = ""
    dtoken_after: str = ""

    @property
    def changed(self) -> bool:
        """两个 dtoken 都非空且不同 —— 即「这次算一次播放会话」。"""
        return (
            bool(self.dtoken_before)
            and bool(self.dtoken_after)
            and self.dtoken_before != self.dtoken_after
        )
