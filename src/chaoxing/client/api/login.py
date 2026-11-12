"""登录链路：登录页 →（按需）验证码 → 提交凭据 → 门户页校验。

四步的意义（legacy 的线上实测结论，逐条都有原因）：

1. ``GET /login`` 预取 ``fid`` 与初始 cookie；
2. 验证码必须**反应式**处理 —— ``/num/code`` 在任何时刻都能取到图片，
   所以"取得到图"不代表本次登录需要验证码；先提交，服务端索要时再取；
3. ``POST /fanyalogin`` 的响应 ``content-type`` 是 ``text/html``，正文却是 JSON，
   且**错误信息在 ``msg2``**（不是 ``msg``）—— 解析绝不能依赖 content-type；
4. ``GET i.chaoxing.com/base`` 才算"登录是否真的生效"：未登录会被重定向回登录页。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from chaoxing.client.api.base import ApiModule
from chaoxing.client.constants import DOMAIN_PORTAL
from chaoxing.client.model.base import Envelope
from chaoxing.client.session import SessionState

log = logging.getLogger(__name__)

#: 服务端暗示"需要验证码"的关键词
CAPTCHA_HINTS = ("验证码", "captcha", "滑动", "滑块", "验证失败")

#: 凭据类错误（不要重试，直接报给用户）
CREDENTIAL_HINTS = (
    "用户名或密码",
    "账号或密码",
    "密码错误",
    "账号不存在",
    "用户不存在",
    "登录失败",
    "禁止登录",
)

#: 风控类错误（等待或换出口，重试无意义）
RISK_HINTS = ("冻结", "锁定", "频繁", "风控", "异常登录")


def encode_password(password: str, mode: str = "plain") -> str:
    """按配置对密码做轻度编码。

    学习通各版本前端分别使用明文 / Base64 / MD5；**默认明文**。若密码确定正确却
    始终失败，依次切换此配置即可 —— 这正是把编码方式做成可配置项的原因。

    :param password: 明文密码
    :param mode: ``plain`` / ``base64`` / ``md5``
    :returns: 送到服务端的形式
    """
    if mode == "base64":
        return base64.b64encode(password.encode("utf-8")).decode("ascii")
    if mode == "md5":
        return hashlib.md5(password.encode("utf-8")).hexdigest()
    return password


@dataclass(slots=True)
class LoginResult:
    """登录结果（可直接展示给用户/网页）。

    :param ok: 是否成功
    :param message: 人读信息
    :param reason: 归类（captcha / risk / credential / unknown）
    :param uid: 成功时的用户 ID
    :param need_human: 是否需要人工介入（验证码等）
    """

    ok: bool
    message: str = ""
    reason: str = ""
    uid: str = ""
    need_human: bool = False


def classify_login_failure(message: str) -> LoginResult:
    """把服务端说辞归类成"人能据此行动"的四种结果。

    分类规则是登录链路上最容易出错、也最值得回归的一环，因此做成**纯函数**：
    不依赖任何实例状态，可被离线测试直接覆盖。

    :param message: 服务端返回的原因（通常来自 ``msg2``）
    :returns: 归一化后的登录结果
    """
    text = (message or "").strip()
    if any(hint in text for hint in CAPTCHA_HINTS):
        return LoginResult(
            ok=False, message=text or "需要验证码", reason="captcha", need_human=True
        )
    if any(hint in text for hint in RISK_HINTS):
        return LoginResult(
            ok=False,
            message=f"{text}（建议稍后重试、更换网络出口，或改用 Cookie 导入）",
            reason="risk",
        )
    if text:
        return LoginResult(ok=False, message=text, reason="credential")
    return LoginResult(ok=False, message="登录被拒绝（服务端未说明原因）", reason="unknown")


def parse_json_body(text: str) -> dict[str, Any]:
    """从"看起来是 HTML 其实是 JSON"的正文里取字典。

    :param text: 响应正文
    :returns: 解析出的字典；失败返回空字典（调用方据空字典走 unknown 分支）
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class LoginApi(ApiModule):
    """登录相关接口（无状态，可服务多个会话）。"""

    async def fetch_login_page(self, session: SessionState) -> str:
        """打开登录页，预取 ``fid`` 与初始 cookie。

        :param session: 账号会话
        :returns: 登录页 HTML
        """
        response = await self._get(session, "login_page")
        return response.text

    async def fetch_captcha(self, session: SessionState) -> bytes:
        """取图形验证码图片（注意：任何时刻都能取到，不代表本次需要）。

        :param session: 账号会话
        :returns: 图片字节
        """
        response = await self._get(session, "captcha_image")
        return response.content

    async def submit_credentials(
        self,
        session: SessionState,
        username: str,
        password: str,
        *,
        captcha: str = "",
        encoding: str = "plain",
    ) -> Envelope:
        """提交账号密码。

        :param session: 账号会话
        :param username: 手机号 / 用户名
        :param password: 明文密码（按 ``encoding`` 编码后送出）
        :param captcha: 图形验证码（服务端索要时才有值）
        :param encoding: 密码编码方式
        :returns: 解包后的响应信封
        """
        payload = {
            "fid": "-1",
            "uname": username,
            "password": encode_password(password, encoding),
            "refer": f"{DOMAIN_PORTAL}/base",
            "t": "true",
            "forbidotherlogin": "0",
            "validate": captcha,
            "doubleFactorLogin": "0",
            "independentId": "0",
        }
        response = await self._post(session, "login_submit", data=payload)
        return Envelope.from_payload(parse_json_body(response.text))

    async def verify_session(self, session: SessionState) -> bool:
        """访问门户页判断登录态是否真的生效。

        :param session: 账号会话
        :returns: 未被重定向回登录页即为真
        """
        response = await self._get(session, "portal_entry")
        final_url = str(response.url)
        return not ("/login" in final_url or "passport" in final_url)

    async def login(
        self,
        session: SessionState,
        username: str,
        password: str,
        *,
        captcha: str = "",
        encoding: str = "plain",
    ) -> LoginResult:
        """完整走一遍登录链路。

        :param session: 账号会话（成功后其 ``status`` 会被置为 ``logged_in``）
        :param username: 手机号 / 用户名
        :param password: 明文密码
        :param captcha: 图形验证码
        :param encoding: 密码编码方式
        :returns: :class:`LoginResult`
        """
        await self.fetch_login_page(session)
        envelope = await self.submit_credentials(
            session, username, password, captcha=captcha, encoding=encoding
        )
        if envelope.ok and await self.verify_session(session):
            session.username = username
            session.status = "logged_in"
            session.dirty = True
            uid = session.cookies.get("_uid", "")
            log.info("登录成功 user=%s uid=%s", username, uid)
            return LoginResult(ok=True, message="登录成功", uid=uid)
        return classify_login_failure(envelope.reason)
