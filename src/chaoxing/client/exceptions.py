"""学习通协议层的异常体系。

分层的意义在于**调用方能不能重试、要不要找人**：

* :class:`TransportError` / :class:`RequestBlocked` —— 网络与限流问题，可以退避重试；
* :class:`SessionExpired` —— 登录态没了，需要重新登录；
* :class:`AuthRejected` —— 凭据/风控拒绝，**重试没有意义**，直接报给用户；
* :class:`HumanRequired` —— 验证码 / 滑块 / 人脸，必须人工介入；
* :class:`EnvelopeFailure` —— HTTP 200 但业务失败（学习通最常见的一种"假成功"）；
* :class:`ErrorPageDetected` —— 拿到了错误页（3004 / 温馨提示 / 权限），不能当正常内容解析。
"""

from __future__ import annotations


class ChaoxingError(Exception):
    """协议层异常基类。"""


class TransportError(ChaoxingError):
    """网络层失败（超时、连接错误、5xx）。"""


class RequestBlocked(TransportError):
    """被服务端拒绝：403、限流、风控。

    :param payload: 可选的响应快照（状态码 / 正文），供账号日志保留原始返回
    """

    def __init__(self, message: str, *, payload: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


class SessionExpired(ChaoxingError):
    """登录态失效，需要重新登录。"""


class AuthRejected(ChaoxingError):
    """凭据错误或账号被风控 —— 不重试，直接上报。

    :param message: 服务端给的原因（学习通把错误放在 ``msg2``）
    :param reason: 归类结果（credential / risk / unknown），供上层决定提示文案
    """

    def __init__(self, message: str, *, reason: str = "credential") -> None:
        super().__init__(message)
        self.reason = reason


class HumanRequired(ChaoxingError):
    """需要人工介入：图形验证码、滑块、人脸抓拍、短信验证。

    :param reason: 具体的介入类型（captcha / slider / face / sms）
    """

    def __init__(self, message: str, *, reason: str = "captcha") -> None:
        super().__init__(message)
        self.reason = reason


class EnvelopeFailure(ChaoxingError):
    """业务失败：HTTP 200，但响应体里的状态是 false。

    :param payload: 原始响应体（脱敏前），便于排障
    """

    def __init__(self, message: str, *, payload: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


class ErrorPageDetected(ChaoxingError):
    """命中了学习通的错误页指纹（此时页面内容不具解析价值）。

    :param marker: 命中的指纹（例如 ``3004``）
    """

    def __init__(
        self,
        message: str,
        *,
        marker: str = "",
        payload: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.marker = marker
        self.payload = payload or {}
