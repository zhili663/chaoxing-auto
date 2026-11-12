"""API 模块基类：一个 domain 一个模块，模块只懂自己那几个端点。

约定（见 ``docs/REFACTOR_PLAN.md`` §5.4）：

* 模块**不持有 session**，session 由调用方按请求传入 —— 这样同一个模块实例可以
  服务多个账号，也不会出现"某处缓存了别人的 cookie"这类串号事故；
* 端点一律经 :func:`chaoxing.client.endpoints.get` 取名获取，不在业务代码里写 URL；
* 返回值优先是 pydantic 模型或响应对象，**不在这里做业务判断**（那是 core 层的事）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from chaoxing.client import endpoints as ep
from chaoxing.client.session import SessionState
from chaoxing.client.transport import ChaoxingTransport

#: 无 charset 声明时的候选编码（顺序即优先级）
FALLBACK_ENCODINGS: tuple[str, ...] = ("utf-8", "gbk")


def decode_response(response: httpx.Response) -> str:
    """把响应正文解成文本。

    优先信任**响应头声明的 charset**，没声明时先试 UTF-8，最后才退 GBK
    （历史版本的学习通页面确实是 GBK）。

    **不要写死编码**：学习通改版后同一个接口的编码会变。写死一个的后果是
    整页中文乱码 —— 形态很好认，UTF-8 字节被当 GBK 读会出现「澶у︾敓」这类字。
    这类错误在 mock 测试里看不出来（fixture 是我们自己编的），只有真账号才暴露。

    :param response: httpx 响应
    :returns: 解码后的文本（任何情况下都返回字符串，不抛异常）
    """
    declared = response.charset_encoding
    if declared:
        try:
            return response.content.decode(declared)
        except (LookupError, UnicodeDecodeError):
            pass
    for encoding in FALLBACK_ENCODINGS:
        try:
            return response.content.decode(encoding, errors="strict")
        except UnicodeDecodeError:
            continue
    return response.content.decode("utf-8", errors="replace")


class ApiModule:
    """所有 domain API 模块的基类。

    :param transport: 共享的传输层
    """

    def __init__(self, transport: ChaoxingTransport) -> None:
        self._transport = transport

    @property
    def transport(self) -> ChaoxingTransport:
        """底层传输层（少数需要自定义请求的场景使用）。"""
        return self._transport

    async def _get(
        self,
        session: SessionState,
        name: str,
        *,
        path_params: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        follow_redirects: bool | None = None,
    ) -> httpx.Response:
        """按注册名发起 GET。

        端点模板里的 ``{object_id}`` 这类路径占位符由 ``path_params`` 渲染 ——
        忘了传的话请求 URL 会留着花括号字面量。

        :param session: 账号会话
        :param name: 端点注册名
        :param path_params: URL 模板占位符的值
        :param params: 查询参数（端点模板参数作为默认值）
        :param headers: 额外请求头
        :param follow_redirects: 是否自动跟随跳转（``None`` = 用默认值）。
            要**逐跳检查** ``Location`` 的场景（比如取课程入口的 ``stuenc``）传 ``False``
        :returns: httpx 响应
        """
        spec = ep.get(name)
        url = spec.url_for(**path_params) if path_params else spec.url
        merged = {**spec.params, **dict(params or {})}
        return await self._transport.request(
            session,
            url,
            params=merged,
            headers=headers,
            follow_redirects=follow_redirects,
        )

    async def _post(
        self,
        session: SessionState,
        name: str,
        *,
        path_params: Mapping[str, str] | None = None,
        data: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """按注册名发起 POST（**不重试**，见 transport 的三条纪律）。

        :param session: 账号会话
        :param name: 端点注册名
        :param path_params: URL 模板占位符的值
        :param data: 表单参数
        :param params: 查询参数
        :param headers: 额外请求头
        :returns: httpx 响应
        """
        spec = ep.get(name)
        url = spec.url_for(**path_params) if path_params else spec.url
        merged = {**spec.params, **dict(params or {})}
        return await self._transport.request(
            session, url, method="POST", data=data, params=merged, headers=headers
        )
