"""视频域：路径参数渲染、心跳参数构造、回执判定。

来源：legacy 线上实测 —— ① 心跳 URL 的路径参数是 cpi/dtoken（写成 clazzId/uid 必 403）；
② Referer 必须是播放器页；③ ``videoFaceCaptureEnc`` 必须传空（透传卡片旧值会被判
"人脸校验失败"）；④ 心跳会持续返回 202 且 ``isPassed`` 恒 false（频控）。
"""

from __future__ import annotations

import httpx
import pytest
import respx

from chaoxing.client import ChaoxingTransport, SessionState, VideoApi
from chaoxing.client.api.video import interpret_heartbeat, player_referer
from chaoxing.client.model.video import HeartbeatRequest, heartbeat_prerequisites
from chaoxing.config import ChaoxingSettings

MOOC1 = "https://mooc1.chaoxing.com"
STATUS_URL = f"{MOOC1}/ananas/status/abc123"
HEARTBEAT_URL = f"{MOOC1}/mooc-ans/multimedia/log/a/507072787/dtoken-1"


def _api() -> tuple[ChaoxingTransport, VideoApi]:
    transport = ChaoxingTransport(ChaoxingSettings(request_min_interval=0, max_retry=0))
    return transport, VideoApi(transport)


def _request(**overrides: object) -> HeartbeatRequest:
    params: dict[str, object] = {
        "clazz_id": "151148348",
        "user_id": "100000002",
        "job_id": "1601174638012256",
        "object_id": "f7a808970399e8eb76e274ce7b0bf1ca",
        "course_id": "265361351",
        "other_info": "nodeId_1192366344-cpi_507072787-rt_d-ds_1-ff_d-be_0_0-vt_1-v_6-enc_x&courseId=265361351",
        "playing_time": 100,
        "duration": 589,
        "cpi": "507072787",
        "dtoken": "dtoken-1",
    }
    params.update(overrides)
    return HeartbeatRequest(**params)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 元信息
# --------------------------------------------------------------------------- #


@respx.mock
async def test_video_info_renders_path_placeholder() -> None:
    """``{object_id}`` 必须被渲染成真实值，否则请求打到一个带花括号的假地址上。

    同时断言 `fetch_info` **确实先打开过播放器页** —— 少了那一步，拿到的 `dtoken`
    不是播放会话里的那个，心跳必然 403（真账号实测）。
    """
    player_route = respx.get(url__startswith=f"{MOOC1}/ananas/modules/video/index.html").mock(
        return_value=httpx.Response(200, text="<html>player</html>")
    )
    route = respx.get(STATUS_URL).mock(
        return_value=httpx.Response(
            200, json={"status": "success", "dtoken": "dtoken-1", "duration": 589}
        )
    )
    transport, api = _api()

    info = await api.fetch_info(SessionState(account_id=1), object_id="abc123")

    assert player_route.called, "取视频元信息前必须先打开播放器页"
    assert route.called
    assert info.dtoken == "dtoken-1"
    assert info.duration == 589
    assert info.usable
    assert "{" not in str(respx.calls[0].request.url)
    await transport.aclose()


# --------------------------------------------------------------------------- #
# 心跳参数构造
# --------------------------------------------------------------------------- #


def test_heartbeat_params_cover_the_documented_fields() -> None:
    """参数集合与实测口径一致（少一个都可能被拒）。"""
    _, api = _api()

    params = api.build_heartbeat_params(_request())

    for key in (
        "clazzId",
        "playingTime",
        "duration",
        "clipTime",
        "objectId",
        "otherInfo",
        "courseId",
        "jobid",
        "userid",
        "isdrag",
        "view",
        "dtype",
        "rt",
        "_t",
        "enc",
    ):
        assert key in params, f"心跳缺少参数 {key}"
    assert params["isdrag"] == "0"  # 真机：起播后正常播放为 0
    assert params["view"] == "json"
    assert params["clipTime"] == "0_589"


def test_optional_params_are_absent_when_card_has_none() -> None:
    """卡片里没有的可选参数，URL 上**一个都不许出现**。

    这条是真账号踩出来的：``videoFaceCaptureEnc`` / ``attDuration`` / ``attDurationEnc``
    是"卡片里有才传"的参数，写成空值常量会让 URL 上挂一串 ``attDuration=`` ——
    服务端直接 403，而且**403 页面不给任何原因**，只能靠与参考实现逐参数比对才发现。
    """
    _, api = _api()

    params = api.build_heartbeat_params(_request(extra={}))

    for key in ("videoFaceCaptureEnc", "attDuration", "attDurationEnc"):
        assert key not in params, f"{key} 不该以空值出现"


def test_optional_params_from_card_are_kept() -> None:
    """卡片里**真带了**的可选值要原样上报（比如需要人脸校验的视频）。"""
    _, api = _api()

    params = api.build_heartbeat_params(
        _request(extra={"videoFaceCaptureEnc": "57946494abcdef", "attDuration": "236"})
    )

    assert params["videoFaceCaptureEnc"] == "57946494abcdef"
    assert params["attDuration"] == "236"


def test_playing_time_is_reported_as_integer_seconds() -> None:
    """上报的 playingTime 是整数秒，且与签名用的值一致。"""
    _, api = _api()

    params = api.build_heartbeat_params(_request(playing_time=100.7))

    assert params["playingTime"] == "100"


# --------------------------------------------------------------------------- #
# 回执判定
# --------------------------------------------------------------------------- #


def _response(status: int, **kwargs: object) -> httpx.Response:
    return httpx.Response(
        status,
        request=httpx.Request("GET", HEARTBEAT_URL),
        **kwargs,  # type: ignore[arg-type]
    )


def test_verdict_passed_on_is_passed_true() -> None:
    """服务端说 ``isPassed: true`` 才算完成。"""
    verdict = interpret_heartbeat(_response(200, json={"isPassed": True}), duration=589)

    assert verdict.passed is True


def test_verdict_passed_on_progress_reaching_duration() -> None:
    """progress 达到时长（允许 1 秒误差）也算完成。"""
    assert interpret_heartbeat(_response(200, json={"progress": 589}), duration=589).passed
    assert interpret_heartbeat(_response(200, json={"progress": 588}), duration=589).passed
    assert not interpret_heartbeat(_response(200, json={"progress": 500}), duration=589).passed


def test_verdict_throttled_on_202() -> None:
    """202 = 收下但不处理（实测返回 HTML 提示页），大概率频控 —— 要退避而不是硬冲。"""
    verdict = interpret_heartbeat(_response(202, text="<html>请稍后再试</html>"))

    assert verdict.throttled is True
    assert verdict.retryable is True


def test_verdict_forbidden_on_403() -> None:
    """403 = 签名/参数被拒（enc、dtoken 过期或 rt 非页面值）。"""
    verdict = interpret_heartbeat(_response(403, text="forbidden"))

    assert verdict.forbidden is True
    assert verdict.ok is False


def test_verdict_face_required_wins_over_status() -> None:
    """人脸抓拍优先于状态码判定（继续推进只会拿到 403）。"""
    verdict = interpret_heartbeat(_response(200, json={"videoFaceCapture": 1}))

    assert verdict.face_required is True
    assert verdict.needs_human is True


def test_verdict_switch_endpoint_on_parameter_error() -> None:
    """主通道报参数错误时切备用域名。"""
    verdict = interpret_heartbeat(_response(200, text="params error"))

    assert verdict.switch_endpoint is True


def test_verdict_unparsable_body_is_not_passed() -> None:
    """**不可解析 ≠ 完成**：宁可重试，也不谎报。"""
    verdict = interpret_heartbeat(_response(200, text=""))

    assert verdict.passed is False
    assert verdict.ok is True  # 没报错，但也没完成


def test_verdict_textual_finished_marker() -> None:
    """少数课程的回执是纯文本。"""
    assert interpret_heartbeat(_response(200, text="该任务点已完成")).passed is True


# --------------------------------------------------------------------------- #
# 完整心跳调用
# --------------------------------------------------------------------------- #


@respx.mock
async def test_heartbeat_sends_player_referer_and_accepts_pass() -> None:
    """真实心跳请求：URL 带 cpi/dtoken、Referer 是播放器页、回执被正确解读。

    真机 2026-10-14（用户抓包）：``mooc-ans/multimedia/log`` 的实测口径是
    ``Accept: */*`` + ``Content-Type: application/json``，且**不带** ``X-Requested-With``/``Origin``
    —— 别拿测验提交那套 XHR 头来套，套错了照样 403。
    """
    route = respx.get(HEARTBEAT_URL).mock(
        return_value=httpx.Response(200, json={"isPassed": True, "progress": 589})
    )
    transport, api = _api()

    verdict = await api.heartbeat(SessionState(account_id=1), _request())

    assert verdict.passed is True
    sent = respx.calls[0].request
    assert sent.headers["referer"] == player_referer("Video")
    assert sent.headers["accept"] == "*/*"
    assert sent.headers["content-type"] == "application/json"
    assert "x-requested-with" not in sent.headers
    assert "origin" not in sent.headers
    assert "enc=" in str(sent.url)
    assert route.called
    await transport.aclose()


@respx.mock
async def test_heartbeat_uses_alt_endpoint_when_asked() -> None:
    """主通道异常时切到 ananas 备用通道。"""
    alt = f"{MOOC1}/ananas/multimedia/log/a/507072787/dtoken-1"
    respx.get(alt).mock(return_value=httpx.Response(200, json={"isPassed": False}))
    transport, api = _api()

    verdict = await api.heartbeat(SessionState(account_id=1), _request(), use_alt_endpoint=True)

    assert verdict.passed is False
    assert "ananas" in str(respx.calls[0].request.url)
    await transport.aclose()


async def test_heartbeat_without_tokens_fails_fast() -> None:
    """缺 cpi/dtoken 时立即报错，而不是发出去等 403。"""
    transport, api = _api()

    with pytest.raises(ValueError, match="cpi"):
        await api.heartbeat(SessionState(account_id=1), _request(cpi="", dtoken=""))

    await transport.aclose()


# --------------------------------------------------------------------------- #
# 激活探测（dtoken 资格：真机对照见 docs/MOOC_ANS_403.md §五）
# --------------------------------------------------------------------------- #


@respx.mock
async def test_probe_activation_detects_new_dtoken() -> None:
    """打开播放器页后 dtoken 变了 = 本次会话具备心跳资格（真机对照见 MOOC_ANS_403 §五）。"""
    respx.get(STATUS_URL).mock(
        side_effect=[
            httpx.Response(200, json={"status": "success", "dtoken": "before", "duration": 441}),
            httpx.Response(200, json={"status": "success", "dtoken": "after", "duration": 441}),
        ]
    )
    player = respx.get(url__startswith=f"{MOOC1}/ananas/modules/video/index.html").mock(
        return_value=httpx.Response(200, text="<html>player</html>")
    )
    transport, api = _api()

    probe = await api.probe_activation(SessionState(account_id=1), object_id="abc123")

    assert (probe.dtoken_before, probe.dtoken_after) == ("before", "after")
    assert probe.changed is True
    assert player.called
    await transport.aclose()


@respx.mock
async def test_probe_activation_reports_unchanged_dtoken() -> None:
    """dtoken 没变 —— 很可能是从未被真实播放过的视频。"""
    respx.get(STATUS_URL).mock(
        side_effect=[
            httpx.Response(200, json={"status": "success", "dtoken": "same"}),
            httpx.Response(200, json={"status": "success", "dtoken": "same"}),
        ]
    )
    respx.get(url__startswith=f"{MOOC1}/ananas/modules/video/index.html").mock(
        return_value=httpx.Response(200, text="<html>player</html>")
    )
    transport, api = _api()

    probe = await api.probe_activation(SessionState(account_id=1), object_id="abc123")

    assert probe.changed is False
    await transport.aclose()


@respx.mock
async def test_probe_activation_without_dtoken_is_not_activated() -> None:
    """status 没给 dtoken 时不得判定为「已激活」。"""
    respx.get(STATUS_URL).mock(return_value=httpx.Response(200, json={"status": "fail"}))
    respx.get(url__startswith=f"{MOOC1}/ananas/modules/video/index.html").mock(
        return_value=httpx.Response(200, text="<html>player</html>")
    )
    transport, api = _api()

    probe = await api.probe_activation(SessionState(account_id=1), object_id="abc123")

    assert probe.changed is False
    await transport.aclose()


# --------------------------------------------------------------------------- #
# 卡片字段完整性（缺一个就可能 403）
# --------------------------------------------------------------------------- #


def test_heartbeat_prerequisites_lists_missing_card_fields() -> None:
    """缺哪个卡片字段就报哪个 —— 这些字段缺一个就可能 403。"""
    payload = {
        "clazzId": "1",
        "courseId": "2",
        "userid": "3",
        "otherInfo": "x",
        "cpi": "4",
        "attDuration": "441",
    }

    assert heartbeat_prerequisites(payload) == ("attDurationEnc",)


def test_heartbeat_prerequisites_accepts_real_sample() -> None:
    """真机心跳样本（MOOC_ANS_403 §四）的卡片字段必须判为齐全。"""
    payload = {
        "clazzId": "155339185",
        "courseId": "265918108",
        "userid": "471652995",
        "otherInfo": "nodeId_1214035150-cpi_581371075-rt_d-ds_1-ff_d-be_0_0-vt_1-v_6-enc_x",
        "cpi": "581371075",
        "attDuration": "441",
        "attDurationEnc": "38abb0e752333cfbce1e913648db8825",
    }

    assert heartbeat_prerequisites(payload) == ()


def test_build_heartbeat_params_normalizes_extra_formats() -> None:
    """透传字段要按**真实心跳的格式**发：``attDuration`` 取整、``courseEngineInfo`` 小写 false。

    实测（2026-10-15 真实节奏实验）：这两个格式不对时，心跳虽然 200，但回执永远
    ``isPassed:false``；格式正确、且进度与真实时间匹配后立刻翻成 ``isPassed:true``。
    """
    from chaoxing.client.api.video import VideoApi
    from chaoxing.client.model.video import HeartbeatRequest

    request = HeartbeatRequest(
        clazz_id="1",
        cpi="2",
        dtoken="3",
        object_id="4",
        other_info="nodeId_1-cpi_2-enc_x",
        course_id="5",
        job_id="6",
        user_id="7",
        playing_time=0.0,
        duration=74.0,
        rate="1.0",
        dtype="Video",
        extra={
            "attDuration": 74.0,
            "attDurationEnc": "abc",
            "courseEngineInfo": None,
            "videoFaceCaptureEnc": "",
        },
    )

    params = VideoApi.build_heartbeat_params(None, request)

    assert params["attDuration"] == "74"
    assert params["courseEngineInfo"] == "false"
    assert params["attDurationEnc"] == "abc"
    assert params["videoFaceCaptureEnc"] == ""
