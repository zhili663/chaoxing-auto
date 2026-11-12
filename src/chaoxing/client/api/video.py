"""视频域：元信息（dtoken）与播放进度上报。

核心约束（全部来自 legacy 线上实测，写错任何一条都会 403 或"心跳不被确认"）：

* **只发心跳，不下载任何视频流** —— 不请求 ``m3u8`` / ``.ts`` / ``.mp4``；
* 心跳 URL 的路径参数是 **cpi 与 dtoken**（早期误用 clazzId / uid 一律 403）；
* 必须带**播放器页 Referer**，且版本号要新（旧版本号同样 403）；
* ``videoFaceCaptureEnc`` **必须传空** —— 卡片里那个旧值透传过去会被服务端判定为
  "人脸校验失败"并返回 403，这是整条链路上最隐蔽的坑；
* 判定以**服务端回执**为准：宁可重试，也不谎报完成。
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

import httpx

from chaoxing.client.api.base import ApiModule
from chaoxing.client.constants import HEARTBEAT_HEADERS
from chaoxing.client.exceptions import ChaoxingError
from chaoxing.client.model.video import (
    FACE_MARKERS,
    FINISHED_MARKERS,
    SWITCH_MARKERS,
    ActivationProbe,
    HeartbeatRequest,
    HeartbeatVerdict,
    VideoInfo,
)
from chaoxing.client.session import SessionState
from chaoxing.client.signature import (
    AUDIO_PLAYER_REFERER,
    VIDEO_PLAYER_REFERER,
    now_ms,
    video_log_enc,
)

log = logging.getLogger(__name__)


def player_referer(dtype: str = "Video") -> str:
    """按媒体类型返回播放器页 Referer（心跳必备，否则 403）。

    :param dtype: ``Video`` / ``Audio``
    :returns: 播放器页面地址
    """
    return AUDIO_PLAYER_REFERER if str(dtype).lower() == "audio" else VIDEO_PLAYER_REFERER


def _as_float(value: Any) -> float:
    """宽容的数值转换（失败返回 0.0）。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def interpret_heartbeat(response: httpx.Response, *, duration: float = 0.0) -> HeartbeatVerdict:
    """解读心跳回执。

    学习通不同课程的回执格式不同（JSON / 纯文本 / 空体），因此采用多信号判定；
    **无法解析时按"未通过"处理** —— 宁可退避重试，也不能谎报任务点完成。

    :param response: 心跳响应
    :param duration: 本次上报的视频总时长（秒），用于按进度判定完成
    :returns: :class:`~chaoxing.client.model.video.HeartbeatVerdict`
    """
    text = (response.text or "")[:2000]
    verdict = HeartbeatVerdict(status_code=response.status_code, raw=text)
    lowered = text.lower()

    if any(marker.lower() in lowered for marker in FACE_MARKERS):
        verdict.face_required = True
        return verdict

    if response.status_code == 403:
        verdict.forbidden = True
        return verdict

    if response.status_code == 202:
        # 202 = 服务端收下但不处理（实测返回 HTML 提示页），大概率是频控；
        # 标记出来让上层退避，而不是按原节奏硬冲。
        verdict.throttled = True
        return verdict

    if response.status_code >= 400 or any(marker.lower() in lowered for marker in SWITCH_MARKERS):
        verdict.switch_endpoint = True
        return verdict

    try:
        data = response.json()
    except ValueError:
        data = None

    if isinstance(data, dict):
        if data.get("isPassed") in (True, "true", 1, "1"):
            verdict.passed = True
            return verdict
        progress = data.get("progress")
        if isinstance(progress, (int, float)) and duration > 0 and float(progress) >= duration - 1:
            verdict.passed = True
            return verdict
        return verdict

    if any(word in text for word in FINISHED_MARKERS):
        verdict.passed = True
    return verdict


class VideoApi(ApiModule):
    """视频元信息与心跳上报。"""

    async def open_player(self, session: SessionState) -> None:
        """打开播放器页 —— **心跳的前置条件**，不是可选项。

        少这一步，``/ananas/status`` 签发的是 **未进入播放会话** 的 ``dtoken``，
        拿它去心跳只会得到 403 —— 而 403 的响应体是个通用错误页，不给任何原因。
        实测对照（同一账号、同一参数、同一签名）：

        * 不打开播放器页 → ``dtoken=8d7798fe5f873cb76be6926f2e6cfd47`` → **403**
        * 打开播放器页   → ``dtoken=9eb1bcaeede460b220c37412950e77bc`` → **200**

        也就是说：服务端认的不是请求的形状，而是**这次请求是否属于一次播放会话**。
        """
        try:
            await self._get(session, "video_player")
        except ChaoxingError as exc:
            log.warning("打开播放器页失败（后续心跳大概率被拒）：%s", exc)

    async def fetch_status(self, session: SessionState, *, object_id: str) -> VideoInfo:
        """只取元信息（**不**打开播放器页）—— 供激活探测取「打开前」的 dtoken。

        :param session: 账号会话
        :param object_id: 视频对象 ID
        :returns: :class:`~chaoxing.client.model.video.VideoInfo`
        """
        response = await self._get(session, "video_info", path_params={"object_id": object_id})
        return VideoInfo.model_validate(response.json())

    async def fetch_info(self, session: SessionState, *, object_id: str) -> VideoInfo:
        """取视频元信息（含 **dtoken** 与真实时长）。

        **内部会先打开播放器页** —— 那一步决定了拿到的 ``dtoken`` 是否可用于心跳
        （详见 :meth:`open_player`）。

        :param session: 账号会话
        :param object_id: 视频对象 ID
        :returns: :class:`~chaoxing.client.model.video.VideoInfo`
        """
        await self.open_player(session)
        return await self.fetch_status(session, object_id=object_id)

    async def probe_activation(self, session: SessionState, *, object_id: str) -> ActivationProbe:
        """对比「打开播放器页前后」两次签发的 dtoken。

        只做诊断、不发心跳：``changed`` 为假说明这个视频多半从未被真实播放过
        （真机对照见 ``docs/MOOC_ANS_403.md`` §五），此时心跳必 403，需要人工播一次。

        :param session: 账号会话
        :param object_id: 视频对象 ID
        :returns: :class:`~chaoxing.client.model.video.ActivationProbe`
        """
        before = await self.fetch_status(session, object_id=object_id)
        await self.open_player(session)
        after = await self.fetch_status(session, object_id=object_id)
        return ActivationProbe(
            object_id=object_id,
            dtoken_before=before.dtoken,
            dtoken_after=after.dtoken,
        )

    def build_heartbeat_params(self, request: HeartbeatRequest) -> dict[str, str]:
        """构造心跳查询参数（纯函数，便于离线回归）。

        :param request: 心跳请求数据
        :returns: 查询参数字典
        """
        playing_time = int(request.playing_time)
        duration = int(request.duration)
        params: dict[str, str] = {
            "clazzId": request.clazz_id,
            "playingTime": str(playing_time),
            "duration": str(duration),
            "clipTime": f"0_{duration}",
            "objectId": request.object_id,
            # ★ 兜底：otherInfo 必须是 ``nodeId_…-enc_<hash>`` 为止，**不能带**
            #   页面里多挂的 ``&courseId=<id>``（那是独立查询参数）。
            #   库里早先扫下的 payload 带着这条尾巴、照发必 403 —— 在这里截掉，
            #   老数据不必重扫即可恢复正常（实测见 docs/MOOC_ANS_403.md）。
            "otherInfo": request.other_info.split("&", 1)[0],
            "courseId": request.course_id,
            "jobid": request.job_id,
            "userid": request.user_id,
            # 真机 App（2026-11-11 抓包）：起播首跳 isdrag=3；后续正常播放 isdrag=0。
            "isdrag": "3" if playing_time == 0 else "0",
            # 真机 App 走 JSON 回执；Web 的 view=pc 不适用于手机链路。
            "view": "json",
            "akid": "null",
            "dtype": request.dtype,
            "rt": request.rate,
            "_t": now_ms(),
            "enc": video_log_enc(
                clazz_id=request.clazz_id,
                user_id=request.user_id,
                job_id=request.job_id,
                object_id=request.object_id,
                playing_time=playing_time,
                duration=duration,
            ),
        }
        # 卡片透传字段只做**补缺**、不许覆盖上面算好的关键字段。
        # 注意别把它们当成"可选"删掉：真实播放器的 URL 上
        # `videoFaceCaptureEnc=`、`attDuration=`、`attDurationEnc=` 都是**照发不误**的
        # （依据是实机抓到的真实心跳，见 docs/MOOC_ANS_403.md）。
        for key, value in request.extra.items():
            if key == "attDuration":
                # 真实心跳里是**整数秒**（"441"），页面/库里常是浮点（"441.0"）—— 照抄浮点拿不到完成标记
                with contextlib.suppress(TypeError, ValueError):
                    value = str(int(float(value)))
            elif key == "courseEngineInfo":
                # 页面里可能是 None / False，真实心跳一律是小写 false
                value = "true" if str(value).lower() in ("true", "1") else "false"
            params.setdefault(key, str(value))
        return params

    async def heartbeat(
        self,
        session: SessionState,
        request: HeartbeatRequest,
        *,
        use_alt_endpoint: bool = False,
    ) -> HeartbeatVerdict:
        """上报一次播放进度。

        :param session: 账号会话
        :param request: 心跳请求数据（含 cpi 与 dtoken 两个**路径参数**）
        :param use_alt_endpoint: 是否走 ananas 备用通道
        :returns: 回执判定结果
        :raises ValueError: 缺少 cpi 或 dtoken（没有它们必然 403，早点报错更好）
        """
        if not request.cpi or not request.dtoken:
            raise ValueError("心跳缺少 cpi 或 dtoken：这两个是 URL 路径参数，必然被 403")

        name = "video_heartbeat_alt" if use_alt_endpoint else "video_heartbeat"
        response = await self._get(
            session,
            name,
            path_params={"cpi": request.cpi, "dtoken": request.dtoken},
            params=self.build_heartbeat_params(request),
            headers={**HEARTBEAT_HEADERS, "Referer": player_referer(request.dtype)},
        )
        verdict = interpret_heartbeat(response, duration=request.duration)
        if not verdict.ok:
            log.info(
                "心跳未通过 job=%s status=%s %s",
                request.job_id,
                response.status_code,
                verdict.as_detail(),
            )
        return verdict
