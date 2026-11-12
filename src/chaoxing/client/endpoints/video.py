"""视频进度端点（核心：只发心跳，不下载任何视频流）。"""

from __future__ import annotations

from chaoxing.client.constants import DOMAIN_MOOC1
from chaoxing.client.endpoints.base import Calibration, Endpoint

VIDEO_INFO = Endpoint(
    name="video_info",
    url=f"{DOMAIN_MOOC1}/ananas/status/{{object_id}}",
    calibration=Calibration.VERIFIED,
    required_params=("object_id",),
    note=(
        "视频元信息。【交叉验证确认】返回 {status:'success', dtoken, duration, ...}。"
        "**dtoken 是心跳 URL 的路径参数**且有有效期，长视频需要定期重新获取"
    ),
    params={"k": "-1", "flag": "normal"},
)

#: 心跳参数模板：发送时与卡片透传字段合并，**只覆盖进度相关字段**
HEARTBEAT_FIELDS: dict[str, str] = {
    "clazzId": "",
    "playingTime": "",  # 当前播放位置（秒）
    "duration": "",  # 总时长（秒）
    "clipTime": "",  # "0_{duration}"
    "objectId": "",
    "otherInfo": "",  # ★ 卡片签发的参数串（含旧式校验值）
    "courseId": "",
    "jobid": "",
    "userid": "",
    "isdrag": "3",  # 3 = 正常播放（非拖动）
    "view": "pc",
    "enc": "",  # ★ 由 signature 计算
    "dtype": "Video",
    "rt": "",  # 播放倍率
    "_t": "",  # 毫秒时间戳
    # ★ 下面这些"看着像可选"的字段，**真实播放器是照发不误的**（`videoFaceCaptureEnc=`
    # 带着空值出现在 URL 上）。依据是实机抓到的真实心跳
    # （`.browser-check/hb-real.json`，见 docs/MOOC_ANS_403.md）。
    # 早先我把它们当"有值才传"删掉过 —— 方向反了。
    "videoFaceCaptureEnc": "",
    "attDuration": "",
    "attDurationEnc": "",
    "courseEngineInfo": "false",
}

VIDEO_PLAYER = Endpoint(
    name="video_player",
    url=f"{DOMAIN_MOOC1}/ananas/modules/video/index.html",
    calibration=Calibration.VERIFIED,
    note=(
        "播放器页面。**心跳的前置条件**：同一份 `/ananas/status` 请求，"
        "在播放器页被访问过之前签发的是 ``dtoken=8d7798fe…``（心跳必 403），"
        "访问之后签发的是 ``dtoken=9eb1bcae…``（心跳 200）。"
        "实测对照：两个 dtoken 差的不是值而是**资格** —— 后者才算「这次是一次播放」。"
        "版本号需与当前线上一致，改版后从真实播放器请求的 Referer 里取。"
    ),
    params={"v": "2026-0920-1154"},
)

VIDEO_HEARTBEAT = Endpoint(
    name="video_heartbeat",
    url=f"{DOMAIN_MOOC1}/mooc-ans/multimedia/log/a/{{cpi}}/{{dtoken}}",
    calibration=Calibration.VERIFIED,
    required_params=("cpi", "dtoken", "objectId", "otherInfo", "enc"),
    note=(
        "播放进度上报。【交叉验证确认】**路径参数是 cpi 与 dtoken**"
        "（早期误用 clazzId/uid 一律 403）；必须携带播放器页 Referer。"
        "本模块只调此接口，绝不请求 m3u8 / ts"
    ),
    params=dict(HEARTBEAT_FIELDS),
)

VIDEO_HEARTBEAT_ALT = Endpoint(
    name="video_heartbeat_alt",
    url=f"{DOMAIN_MOOC1}/ananas/multimedia/log/a/{{cpi}}/{{dtoken}}",
    calibration=Calibration.TEMPLATE,
    required_params=("cpi", "dtoken", "objectId", "otherInfo", "enc"),
    note="备用上报通道（部分课程走 ananas 域名）；主通道连续异常时切换",
    params=dict(HEARTBEAT_FIELDS),
)
