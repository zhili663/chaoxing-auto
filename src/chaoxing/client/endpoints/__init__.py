"""端点注册表：按 domain 拆分定义，此处只负责汇总与查询。

用法::

    from chaoxing.client import endpoints as ep

    spec = ep.get("login_page")
    url = spec.url_for(object_id="abc")

每个端点都带 :class:`~chaoxing.client.endpoints.base.Calibration` 标记；
``calibration_report()`` 的产物直接喂给 ``docs/PROTOCOL_COVERAGE.md``（见 REFACTOR_PLAN §5.14）。
"""

from __future__ import annotations

from chaoxing.client.endpoints.auth import CAPTCHA_IMAGE, LOGIN_PAGE, LOGIN_SUBMIT, PORTAL_ENTRY
from chaoxing.client.endpoints.base import Calibration, Endpoint
from chaoxing.client.endpoints.course import (
    CHAPTER_CARDS,
    CHAPTER_CARDS_MOOC,
    CHAPTER_CONTENT,
    CHAPTER_COURSE_LIST,
    CHAPTER_STUDY_MOOC2,
    CHAPTER_STUDY_PAGE,
    CHAPTER_TREE_AJAX,
    COURSE_ENTRY,
    COURSE_LIST,
    COURSE_LIST_LEGACY,
    COURSE_TRANSFER,
)
from chaoxing.client.endpoints.document import (
    CHAPTER_DOCUMENT_MARKS,
    DOCUMENT_FINISH,
    READ_FINISH,
    READ_V2_SHOW,
)
from chaoxing.client.endpoints.exam import EXAM_START, EXAM_SUBMIT
from chaoxing.client.endpoints.quiz import (
    QUIZ_DETAIL,
    QUIZ_PHONE_ENTRY,
    QUIZ_PHONE_REVIEW,
    QUIZ_PHONE_SUBMIT,
    QUIZ_PHONE_WORK,
    QUIZ_PLAIN_QUESTIONS,
    QUIZ_REDIRECT,
    QUIZ_SUBMIT,
    QUIZ_VALIDATE,
    QUIZ_VERIFY_CODE,
    QUIZ_VERIFY_IMAGE,
    QUIZ_WORK_HANDLE,
    QUIZ_WORK_PAGE,
)
from chaoxing.client.endpoints.video import (
    VIDEO_HEARTBEAT,
    VIDEO_HEARTBEAT_ALT,
    VIDEO_INFO,
    VIDEO_PLAYER,
)

__all__ = ["REGISTRY", "Calibration", "Endpoint", "calibration_report", "get"]

_ALL: tuple[Endpoint, ...] = (
    # 认证
    LOGIN_PAGE,
    LOGIN_SUBMIT,
    CAPTCHA_IMAGE,
    PORTAL_ENTRY,
    # 课程与章节
    COURSE_LIST,
    COURSE_LIST_LEGACY,
    COURSE_ENTRY,
    CHAPTER_CONTENT,
    CHAPTER_CARDS,
    CHAPTER_STUDY_PAGE,
    CHAPTER_STUDY_MOOC2,
    CHAPTER_TREE_AJAX,
    CHAPTER_COURSE_LIST,
    CHAPTER_CARDS_MOOC,
    COURSE_TRANSFER,
    # 视频
    VIDEO_INFO,
    VIDEO_PLAYER,
    VIDEO_HEARTBEAT,
    VIDEO_HEARTBEAT_ALT,
    # 文档 / 阅读
    DOCUMENT_FINISH,
    READ_FINISH,
    READ_V2_SHOW,
    CHAPTER_DOCUMENT_MARKS,
    # 测验
    QUIZ_REDIRECT,
    QUIZ_PLAIN_QUESTIONS,
    QUIZ_DETAIL,
    QUIZ_PHONE_ENTRY,
    QUIZ_PHONE_WORK,
    QUIZ_PHONE_REVIEW,
    QUIZ_PHONE_SUBMIT,
    QUIZ_SUBMIT,
    QUIZ_WORK_PAGE,
    QUIZ_WORK_HANDLE,
    QUIZ_VALIDATE,
    QUIZ_VERIFY_CODE,
    QUIZ_VERIFY_IMAGE,
    # 考试
    EXAM_START,
    EXAM_SUBMIT,
)

#: 全部端点：注册名 -> 描述
REGISTRY: dict[str, Endpoint] = {spec.name: spec for spec in _ALL}


def get(name: str) -> Endpoint:
    """按名字取端点。

    :param name: 注册名
    :raises KeyError: 名字不存在时显式报错（防止拼写错误静默通过）
    :returns: 端点描述
    """
    try:
        return REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"未注册的端点：{name}；可用：{sorted(REGISTRY)}") from exc


def calibration_report() -> list[dict[str, str]]:
    """生成校准清单（供 CLI / API / 协议覆盖文档消费）。"""
    return [
        {
            "name": spec.name,
            "method": spec.method,
            "url": spec.url,
            "calibration": str(spec.calibration),
            "required_params": ",".join(spec.required_params),
            "note": spec.note,
        }
        for spec in REGISTRY.values()
    ]
