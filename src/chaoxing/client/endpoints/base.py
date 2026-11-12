"""端点描述的公共类型。

为什么端点要单独建模
--------------------
学习通是多子系统 + 频繁改版的站点，接口路径与参数会在改版中漂移。把路径集中成
注册表有两个好处：改版时只改一处；每个端点带 :class:`Calibration` 标记，
明确"哪些已按线上实测验证、哪些是模板待校准"，避免"看起来跑通、其实提交错题"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Calibration(StrEnum):
    """端点置信度。

    * ``VERIFIED`` —— 线上实测确认，参数由页面透传，可直接使用；
    * ``TEMPLATE`` —— 路径可信、参数需用真实抓包核对；
    * ``VOLATILE`` —— 高频改版接口，默认关闭，需显式启用。
    """

    VERIFIED = "verified"
    TEMPLATE = "template"
    VOLATILE = "volatile"


@dataclass(frozen=True, slots=True)
class Endpoint:
    """单个端点的描述。

    :param name: 注册名（全局唯一，调用方用它取端点）
    :param url: 完整 URL，可用 ``{path_param}`` 占位
    :param method: HTTP 方法
    :param calibration: 置信度
    :param required_params: 必须由页面解析提供的参数（缺失即说明解析逻辑要更新）
    :param note: 人类可读的实测结论（改版排查时的第一手线索）
    :param params: **URL 查询参数**模板（会拼进 URL）。表单字段请走 ``data`` ——
        写在这里会被当成 query 带上，形成一组空值参数，而不少服务端**优先读 query**，
        于是提交内容被空值覆盖（真账号登录时才会暴露，mock 测试看不出来）
    """

    name: str
    url: str
    method: str = "GET"
    calibration: Calibration = Calibration.TEMPLATE
    required_params: tuple[str, ...] = ()
    note: str = ""
    params: dict[str, str] = field(default_factory=dict)

    def url_for(self, **path_params: str) -> str:
        """填充 URL 模板里的路径参数。

        :param path_params: 例如 ``cpi="123"``、``dtoken="abc"``
        :returns: 渲染后的 URL
        """
        return self.url.format(**path_params)

    def describe(self) -> str:
        """一行式描述（日志与校准报告用）。"""
        return f"[{self.calibration}] {self.method} {self.url}"
