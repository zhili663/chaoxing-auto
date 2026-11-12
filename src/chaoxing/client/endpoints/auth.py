"""认证类端点（passport2 / i 门户）。

实测结论（2026-09）都写在 ``note`` 里，改版排查时先看这里。
"""

from __future__ import annotations

from chaoxing.client.constants import DOMAIN_PASSPORT, DOMAIN_PORTAL
from chaoxing.client.endpoints.base import Calibration, Endpoint

LOGIN_PAGE = Endpoint(
    name="login_page",
    url=f"{DOMAIN_PASSPORT}/login",
    calibration=Calibration.VERIFIED,
    note=(
        "登录页：预取 fid 与初始 cookie，并检测验证码开关。"
        "【实测】GET 200 / 约 32KB / 页面无滑块标记"
    ),
    params={"fid": "", "newversion": "true", "refer": f"{DOMAIN_PORTAL}/"},
)

LOGIN_SUBMIT = Endpoint(
    name="login_submit",
    url=f"{DOMAIN_PASSPORT}/fanyalogin",
    method="POST",
    calibration=Calibration.VERIFIED,
    required_params=("uname", "password"),
    note=(
        "账号密码登录。【实测】响应 content-type 是 text/html 但正文是 JSON，"
        '结构 {"status": bool, "msg2": str} —— 错误信息在 msg2（不是 msg）。'
        "参数编码方式（plain/base64/md5）需按账号实测确定。"
        "⚠️ 表单字段**必须走 data**（见 LoginApi.submit_credentials）："
        "早先把它们写在 params 里，于是请求变成 `?uname=&password=` 这种**空值 query**，"
        "服务端优先读 query → 一律判「用户名或密码错误」——"
        "真账号登录时才暴露，respx 测试只看 URL 路径，发现不了。"
    ),
)

CAPTCHA_IMAGE = Endpoint(
    name="captcha_image",
    url=f"{DOMAIN_PASSPORT}/num/code",
    calibration=Calibration.VERIFIED,
    note=(
        "图形验证码图片。【实测】GET 200 image/jpeg；"
        "**任何时刻都可用**，不代表本次登录需要验证码 —— 所以验证码必须反应式处理"
    ),
)

PORTAL_ENTRY = Endpoint(
    name="portal_entry",
    url=f"{DOMAIN_PORTAL}/base",
    calibration=Calibration.VERIFIED,
    note="登录后的门户页；其响应的 Set-Cookie 才包含完整会话（_uid / _d 等）",
    params={"ws": "1"},
)
