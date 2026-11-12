"""按 domain 拆分的 API 模块。

每个模块对应学习通的一个业务域：login / course / chapter / video / quiz /
exam / document / discussion。模块之间不互相调用 —— 需要跨域的编排属于
``core.services``。
"""

from __future__ import annotations

from chaoxing.client.api.base import ApiModule
from chaoxing.client.api.chapter import ChapterApi
from chaoxing.client.api.course import CourseApi, extract_stuenc
from chaoxing.client.api.document import DocumentApi
from chaoxing.client.api.login import (
    CAPTCHA_HINTS,
    LoginApi,
    LoginResult,
    classify_login_failure,
)
from chaoxing.client.api.quiz import (
    QuizApi,
    build_answerwqbid,
    build_submit_query,
    submit_target,
)
from chaoxing.client.api.video import VideoApi, interpret_heartbeat, player_referer

__all__ = [
    "CAPTCHA_HINTS",
    "ApiModule",
    "ChapterApi",
    "CourseApi",
    "DocumentApi",
    "LoginApi",
    "LoginResult",
    "QuizApi",
    "VideoApi",
    "build_answerwqbid",
    "build_submit_query",
    "classify_login_failure",
    "extract_stuenc",
    "interpret_heartbeat",
    "player_referer",
    "submit_target",
]
