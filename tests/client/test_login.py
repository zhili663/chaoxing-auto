"""登录链路：密码编码、失败分类、四步流程。

来源：① ``fanyalogin`` 的 content-type 是 text/html 但正文是 JSON，错误在 ``msg2``；
② 图形验证码要反应式处理（``/num/code`` 任何时刻都能取到图）；
③ 未登录访问门户页会被重定向回登录页 —— 这才是"登录是否生效"的判据。
"""

from __future__ import annotations

import base64
import hashlib

import httpx
import pytest
import respx

from chaoxing.client import ChaoxingTransport, LoginApi, SessionState, classify_login_failure
from chaoxing.client.api.login import encode_password, parse_json_body
from chaoxing.config import ChaoxingSettings

PASSPORT = "https://passport2.chaoxing.com"
PORTAL = "https://i.chaoxing.com"


def _api() -> tuple[ChaoxingTransport, LoginApi]:
    """构造一个不限速的 transport + LoginApi。"""
    transport = ChaoxingTransport(ChaoxingSettings(request_min_interval=0, max_retry=0))
    return transport, LoginApi(transport)


# --------------------------------------------------------------------------- #
# 纯函数：编码与分类
# --------------------------------------------------------------------------- #


def test_encode_password_supports_three_modes() -> None:
    """学习通各版本前端分别用明文 / Base64 / MD5，三种都要能切。"""
    assert encode_password("secret", "plain") == "secret"
    assert encode_password("secret", "base64") == base64.b64encode(b"secret").decode()
    assert encode_password("secret", "md5") == hashlib.md5(b"secret").hexdigest()


def test_classify_marks_captcha_as_human_required() -> None:
    """验证码要标记为需人工介入，而不是让调度器傻重试。"""
    result = classify_login_failure("请输入验证码后重试")

    assert result.need_human is True
    assert result.reason == "captcha"


def test_classify_detects_risk_control() -> None:
    """风控（冻结/频繁）重试无意义，要给出口建议。"""
    result = classify_login_failure("您的账号已被冻结，请联系管理员")

    assert result.reason == "risk"
    assert "更换网络出口" in result.message


def test_classify_detects_credential_error() -> None:
    """凭据错误就是凭据错误。"""
    result = classify_login_failure("用户名或密码错误")

    assert result.reason == "credential"
    assert result.need_human is False


def test_classify_unknown_message_keeps_text() -> None:
    """服务端说了话但不在已知词表里：原样保留，别丢信息。"""
    assert classify_login_failure("奇怪的错误").reason == "credential"
    assert classify_login_failure("").reason == "unknown"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"status": true, "msg": "ok"}', {"status": True, "msg": "ok"}),
        ("<html>not json</html>", {}),
        ("[1, 2, 3]", {}),
    ],
)
def test_parse_json_body_is_defensive(text: str, expected: dict[str, object]) -> None:
    """正文不是 JSON 或不是对象时返回空字典，绝不抛异常。"""
    assert parse_json_body(text) == expected


# --------------------------------------------------------------------------- #
# 四步流程
# --------------------------------------------------------------------------- #


@respx.mock
async def test_login_success_marks_session_logged_in() -> None:
    """完整链路：登录页 → 提交（HTML content-type + JSON 正文）→ 门户页校验。"""
    respx.get(f"{PASSPORT}/login").mock(return_value=httpx.Response(200, text="<html>login</html>"))
    respx.post(f"{PASSPORT}/fanyalogin").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},  # 实测就是这样
            text='{"status": true, "msg": "登录成功"}',
        )
    )
    respx.get(f"{PORTAL}/base").mock(
        return_value=httpx.Response(
            200, text="portal", headers={"set-cookie": "_uid=100000005; Path=/"}
        )
    )
    transport, api = _api()
    session = SessionState(account_id=1)

    result = await api.login(session, "13800000000", "secret")

    assert result.ok is True
    assert result.uid == "100000005"
    assert session.status == "logged_in"
    assert session.is_authenticated
    await transport.aclose()


@respx.mock
async def test_login_failure_returns_reason_from_msg2() -> None:
    """凭据错误：服务端把原因放在 ``msg2``，必须原样带回来。"""
    respx.get(f"{PASSPORT}/login").mock(return_value=httpx.Response(200, text="<html></html>"))
    respx.post(f"{PASSPORT}/fanyalogin").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='{"msg2": "用户名或密码错误", "status": false}',
        )
    )
    transport, api = _api()
    session = SessionState(account_id=1)

    result = await api.login(session, "13800000000", "wrong")

    assert result.ok is False
    assert result.reason == "credential"
    assert "用户名或密码错误" in result.message
    assert session.status == "pending"  # 失败不得把状态改成已登录
    await transport.aclose()


@respx.mock
async def test_login_requires_captcha_returns_human_flag() -> None:
    """服务端索要验证码：标记 need_human，并且能取到图片。"""
    respx.get(f"{PASSPORT}/login").mock(return_value=httpx.Response(200, text="<html></html>"))
    respx.post(f"{PASSPORT}/fanyalogin").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='{"msg2": "请输入验证码", "status": false}',
        )
    )
    respx.get(f"{PASSPORT}/num/code").mock(
        return_value=httpx.Response(
            200, content=b"\xff\xd8\xff", headers={"content-type": "image/jpeg"}
        )
    )
    transport, api = _api()
    session = SessionState(account_id=1)

    result = await api.login(session, "13800000000", "secret")
    image = await api.fetch_captcha(session)

    assert result.need_human is True
    assert result.reason == "captcha"
    assert image.startswith(b"\xff\xd8")
    await transport.aclose()


@respx.mock
async def test_verify_session_detects_redirect_back_to_login() -> None:
    """门户页被重定向回登录页 ⇒ 登录态无效（不能只看 HTTP 200）。"""
    respx.get(f"{PORTAL}/base").mock(
        return_value=httpx.Response(302, headers={"location": f"{PASSPORT}/login?refer=base"})
    )
    respx.get(f"{PASSPORT}/login").mock(return_value=httpx.Response(200, text="<html></html>"))
    transport, api = _api()

    assert await api.verify_session(SessionState(account_id=1)) is False
    await transport.aclose()
