"""账号域的请求/响应模型。

**凭据只进不出**：``password`` 可以传进来（写请求），但任何响应里都不会回显 ——
连密文都不给。前端要展示的只有"有没有配密码"这个布尔值。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AccountCreate(BaseModel):
    """新增账号。"""

    username: str = Field(min_length=1, max_length=64, description="超星账号（手机号/学号）")
    password: str = Field(default="", max_length=128, description="明文只在此提交，不落库不回声")
    note: str = Field(default="", max_length=255)
    priority: int = Field(default=100, ge=0, le=9999, description="数值小的先刷")
    owner_user_id: int | None = Field(default=None, description="属主（可空=共享账号）")


class AccountUpdate(BaseModel):
    """更新账号（只改传进来的字段）。"""

    password: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=255)
    priority: int | None = Field(default=None, ge=0, le=9999)
    enabled: bool | None = None


class AccountOut(BaseModel):
    """账号信息（**绝不含凭据**）。"""

    id: int
    username: str
    owner_user_id: int | None
    enabled: bool
    priority: int
    note: str
    status: str
    has_password: bool
    has_session: bool
    last_check_at: datetime | None = None


class LoginRequest(BaseModel):
    """触发一次登录（密码可选：不传就用库里已存的）。"""

    password: str | None = Field(default=None, max_length=128)
    captcha: str = Field(default="", max_length=16)


class LoginResultOut(BaseModel):
    """登录结果。"""

    ok: bool
    message: str = ""
    reason: str = ""
    need_human: bool = False
