"""HTTP 层的安全边界：会话 Cookie 与 CSRF。

与 :mod:`chaoxing.utils.security` 的分工：那里是**算法与状态**（哈希、token、nonce），
这里是**框架相关的那一层**（Cookie 属性、Origin 校验）。

两处细节值得记下来：

* Cookie 用 ``HttpOnly``（JS 拿不到 token，XSS 也偷不走会话）+ ``SameSite=Lax``
  （跨站 POST 不带 Cookie，挡掉大部分 CSRF）；**SSE 用同一个 Cookie**，
  所以 ``EventSource`` 可以直接用，不需要自定义头；
* 写操作额外校验 ``Origin`` —— ``SameSite=Lax`` 对同站子域与部分旧浏览器不够，
  多一层显式判断成本极低。
"""

from __future__ import annotations

from fastapi import Request, Response

#: 会话 Cookie 名
SESSION_COOKIE = "cx_session"
#: 会话有效期（秒）：30 天
SESSION_MAX_AGE = 30 * 24 * 3600


def set_session_cookie(response: Response, token: str, *, secure: bool = False) -> None:
    """写入会话 Cookie。

    :param response: FastAPI 响应对象
    :param token: 会话 token（明文只在 Cookie 里出现）
    :param secure: 是否要求 HTTPS（生产开，本地 http 调试关）
    """
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """清除会话 Cookie（登出）。"""
    response.delete_cookie(key=SESSION_COOKIE, path="/")


def read_session_cookie(request: Request) -> str:
    """从请求里取会话 token（没有则空串）。"""
    return request.cookies.get(SESSION_COOKIE, "")


def same_origin(request: Request) -> bool:
    """写操作的 Origin 校验。

    没有 ``Origin`` 头的请求（curl、CLI、同源 GET）放行；带了就必须与本机 Host 一致。

    :param request: 请求
    :returns: 是否可信
    """
    origin = request.headers.get("origin", "")
    if not origin:
        return True
    host = request.headers.get("host", "")
    return origin.rstrip("/").endswith(host)


def require_same_origin(request: Request) -> None:
    """写操作前调用：来源不可信时抛 403。

    :param request: 请求
    :raises chaoxing.api.errors.Forbidden: 来源不可信
    """
    from chaoxing.api.errors import Forbidden

    if not same_origin(request):
        raise Forbidden("拒绝跨站请求（Origin 校验失败）")
