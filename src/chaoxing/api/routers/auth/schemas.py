"""认证域的请求/响应模型。

口径说明：``secret`` 一律是前端 ``sha256(password)`` 的结果（64 位小写十六进制）。
明文口令**不出浏览器**，这也意味着接口层能校验的东西就是"它到底是不是一个哈希值"。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChallengeResponse(BaseModel):
    """一次性 nonce。"""

    nonce: str
    ttl: int
    scene: str


class RegisterRequest(BaseModel):
    """注册请求（需要 nonce —— 低频高危动作防重放）。"""

    username: str = Field(min_length=3, max_length=64)
    secret: str = Field(min_length=64, max_length=64, description="sha256(password)")
    display_name: str = Field(default="", max_length=64)
    #: 手机号（可选）：留空即用 ``NULL``，之后也能用手机号登录
    phone: str | None = Field(default=None, max_length=20, description="可选手机号，用于登录")
    nonce: str = Field(default="", description="``/auth/challenge`` 签发的一次性凭证")


class LoginRequest(BaseModel):
    """登录请求。

    登录**不掺 nonce**（见 :mod:`chaoxing.utils.security` 的说明：服务端只有
    Argon2id(C)，无法校验掺了 nonce 的 proof），安全边界落在 TLS + HttpOnly + 限速上。
    """

    username: str = Field(min_length=1, max_length=64)
    secret: str = Field(min_length=64, max_length=64)


class UserOut(BaseModel):
    """用户信息（含权限集合，前端据此渲染菜单）。"""

    id: int
    username: str
    display_name: str
    is_active: bool
    is_superuser: bool
    permissions: list[str]
    #: 手机号（追加键；既有前端忽略未知键，不会破坏契约）
    phone: str | None = None


class OkOut(BaseModel):
    """通用成功回执。"""

    ok: bool = True
    message: str = ""
