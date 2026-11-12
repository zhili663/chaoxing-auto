"""视频心跳签名：本项目**唯一**需要复刻算法的地方，因此单独立文件、单独立测试。

算法（对照开源实现交叉验证 + 线上实测）::

    enc = md5(
        "[{clazzId}][{userid}][{jobid}][{objectId}]"
        "[{playingTime * 1000}][{SALT}]"
        "[{duration * 1000}][0_{duration}]"
    )

要点：

* ``playingTime`` / ``duration`` 单位是**秒**，参与计算前乘 1000（毫秒）；
* 末段 ``0_{duration}`` 是 ``clipTime`` 的形态（0 秒到总时长）；
* ``SALT`` 是前端硬编码的公开常量。

心跳 URL 的路径参数是 **``cpi`` 与 ``dtoken``**（不是 clazzId / uid）；``dtoken`` 由
``/ananas/status/{objectId}`` 签发且会过期，长视频必须定期刷新。
"""

from __future__ import annotations

import hashlib
import time

#: 前端硬编码的盐值（参与 enc 计算，公开常量）
VIDEO_ENC_SALT = "d_yHJ!$pdA~5"

#: 播放器页面地址 —— 心跳请求的 Referer 必须是它，否则服务端直接 403。
#: **版本号来自真实抓包**（2026-0920-1154）：写旧版本号会被拒 —— 2026-10 实测，
#: 再次改版导致 403 时，用 `cx dev capture browser` 重抓播放器 iframe 的地址换掉这里。
VIDEO_PLAYER_REFERER = "https://mooc1.chaoxing.com/ananas/modules/video/index.html?v=2026-0920-1154"

#: 音频播放器页面地址
AUDIO_PLAYER_REFERER = (
    "https://mooc1.chaoxing.com/ananas/modules/audio/index_new.html?v=2026-0920-1154"
)


def now_ms() -> str:
    """毫秒时间戳字符串（心跳的 ``_t`` 参数）。"""
    return str(int(time.time() * 1000))


def video_log_enc(
    *,
    clazz_id: str,
    user_id: str,
    job_id: str,
    object_id: str,
    playing_time: float,
    duration: float,
) -> str:
    """计算视频心跳的 ``enc`` 签名。

    :param clazz_id: 班级 ID
    :param user_id: 用户 ID（卡片 ``defaults.userid``）
    :param job_id: 任务点 ID
    :param object_id: 视频对象 ID
    :param playing_time: 当前播放位置（秒）
    :param duration: 视频总时长（秒）
    :returns: 32 位 md5 十六进制串
    """
    payload = (
        f"[{clazz_id}][{user_id}][{job_id}][{object_id}]"
        f"[{int(float(playing_time) * 1000)}][{VIDEO_ENC_SALT}]"
        f"[{int(float(duration) * 1000)}][0_{int(float(duration))}]"
    )
    # md5 是协议要求（服务端就认这个），与安全无关。
    return hashlib.md5(payload.encode("utf-8")).hexdigest()
