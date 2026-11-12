"""学习通协议层：常量、端点注册表、传输层、会话注册表、模型、解析器与按 domain 的 API。

最小用法::

    from chaoxing.client import ChaoxingTransport, SessionRegistry, CourseApi, LoginApi
    from chaoxing.config import get_settings

    settings = get_settings()
    transport = ChaoxingTransport(settings.chaoxing)
    registry = SessionRegistry()
    login_api, course_api = LoginApi(transport), CourseApi(transport)

    async with transport:
        async with registry.acquire(account_id=1) as session:
            await login_api.login(session, "13800000000", "secret")
            for course in await course_api.list_courses(session):
                print(course.key, course.title)

设计约束（``docs/REFACTOR_PLAN.md`` §2、§5.4）：

* 本层可脱离 core / storage / api / cli **单独运行**（``cx dev probe`` 就靠它）；
* 一个 transport 服务多个账号，cookie 由 :class:`SessionState` 持有（不用 client jar）；
* API 模块无状态，session 按请求传入；
* 解析全部是纯函数（:mod:`chaoxing.client.parser`），可喂 fixture 离线回归。
"""

from __future__ import annotations

from chaoxing.client import endpoints, model, parser, signature
from chaoxing.client.api import (
    ApiModule,
    ChapterApi,
    CourseApi,
    DocumentApi,
    LoginApi,
    LoginResult,
    QuizApi,
    VideoApi,
    classify_login_failure,
    extract_stuenc,
    interpret_heartbeat,
)
from chaoxing.client.coverage import (
    CoverageRow,
    build_markdown,
    build_rows,
    summarise,
    unmatched_requests,
)
from chaoxing.client.exceptions import (
    AuthRejected,
    ChaoxingError,
    EnvelopeFailure,
    ErrorPageDetected,
    HumanRequired,
    RequestBlocked,
    SessionExpired,
    TransportError,
)
from chaoxing.client.model import (
    ChaoxingModel,
    Course,
    Envelope,
    HeartbeatRequest,
    HeartbeatVerdict,
    JobCard,
    VideoInfo,
    detect_error_page,
)
from chaoxing.client.registry import SessionRegistry, SessionStore
from chaoxing.client.session import SessionState
from chaoxing.client.transport import ChaoxingTransport

__all__ = [
    "ApiModule",
    "AuthRejected",
    "ChaoxingError",
    "ChaoxingModel",
    "ChaoxingTransport",
    "ChapterApi",
    "Course",
    "CourseApi",
    "CoverageRow",
    "DocumentApi",
    "Envelope",
    "EnvelopeFailure",
    "ErrorPageDetected",
    "HeartbeatRequest",
    "HeartbeatVerdict",
    "HumanRequired",
    "JobCard",
    "LoginApi",
    "LoginResult",
    "QuizApi",
    "RequestBlocked",
    "SessionExpired",
    "SessionRegistry",
    "SessionState",
    "SessionStore",
    "TransportError",
    "VideoApi",
    "VideoInfo",
    "build_markdown",
    "build_rows",
    "classify_login_failure",
    "detect_error_page",
    "endpoints",
    "extract_stuenc",
    "interpret_heartbeat",
    "model",
    "parser",
    "signature",
    "summarise",
    "unmatched_requests",
]
