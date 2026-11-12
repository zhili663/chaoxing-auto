"""响应模型与错误页识别：HTTP 200 ≠ 成功的三道防线。

来源：① 缺参时服务端返回 200 但正文 ``{"status": false}``（"无效的请求参数"）；
② 错误信息实测在 ``msg2``；③ 题目页会返回 3004 错误页，被兜底解析器当成题目
（实测把页脚文字解析成一道"题"并准备提交）。
"""

from __future__ import annotations

import pytest

from chaoxing.client.exceptions import EnvelopeFailure, ErrorPageDetected
from chaoxing.client.model import (
    ChaoxingModel,
    Envelope,
    detect_error_page,
    ensure_not_error_page,
)


class _Sample(ChaoxingModel):
    """模拟一个接口响应模型。"""

    object_id: str = ""
    duration: float = 0.0
    extra_note: str = ""


def test_unknown_fields_are_ignored() -> None:
    """学习通随时加字段，多出来的键不能让解析崩。"""
    model = _Sample.from_payload({"objectId": "abc", "brandNewField": 1})

    assert model.object_id == "abc"


def test_camel_and_snake_case_both_accepted() -> None:
    """同一语义在两套命名间漂移，两种都要认。"""
    assert _Sample.from_payload({"objectId": "x"}).object_id == "x"
    assert _Sample.from_payload({"object_id": "y"}).object_id == "y"


def test_numbers_and_strings_interconvert() -> None:
    """``"3"`` 与 ``3`` 都要能进数值/字符串字段，并裁剪空白。"""
    model = _Sample.from_payload({"objectId": 12345, "duration": " 42.5 "})

    assert model.object_id == "12345"
    assert model.duration == 42.5


def test_envelope_treats_missing_status_as_ok() -> None:
    """``status`` 缺失时不武断判失败（有些接口只在失败时给 msg）。"""
    assert Envelope.from_payload({"msg": ""}).ok is True


def test_envelope_require_ok_raises_on_false_status() -> None:
    """真实事故：缺 jtoken 时 200 + ``{"msg":"无效的请求参数","status":false}``。"""
    envelope = Envelope.from_payload({"msg": "无效的请求参数", "status": False})

    with pytest.raises(EnvelopeFailure) as excinfo:
        envelope.require_ok()

    assert "无效的请求参数" in str(excinfo.value)
    assert excinfo.value.payload["status"] is False


def test_envelope_reason_prefers_msg2() -> None:
    """实测：登录失败的原因在 ``msg2``（不是 ``msg``）。"""
    envelope = Envelope.from_payload({"msg": "", "msg2": "用户名或密码错误", "status": False})

    assert envelope.reason == "用户名或密码错误"


@pytest.mark.parametrize(
    "html",
    [
        "<html><body>您长时间没有操作 [3004]</body></html>",
        "<html><body><h1>温馨提示</h1></body></html>",
        "<html><body>enc校验失败</body></html>",
    ],
)
def test_error_page_fingerprints_are_detected(html: str) -> None:
    """错误页指纹必须被识别（否则会把页脚当成题目提交出去）。"""
    assert detect_error_page(html) is not None
    with pytest.raises(ErrorPageDetected):
        ensure_not_error_page(html)


def test_normal_page_passes_error_page_check() -> None:
    """正常页面不能被误判（误判的代价是整门课刷不动）。"""
    ensure_not_error_page("<html><body><div class='chapter'>第一章</div></body></html>")

    assert detect_error_page("") is None
