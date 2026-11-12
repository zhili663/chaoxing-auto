"""心跳签名：本项目唯一复刻算法的地方，必须锁死。

算法写错的表现是**线上全部 403**，没有任何缓冲余地，所以这里用独立构造的期望值
（而不是复用实现里的拼接逻辑）来验证字段顺序与单位换算。
"""

from __future__ import annotations

import hashlib
import time

from chaoxing.client.signature import (
    AUDIO_PLAYER_REFERER,
    VIDEO_ENC_SALT,
    VIDEO_PLAYER_REFERER,
    now_ms,
    video_log_enc,
)


def test_enc_matches_independently_constructed_payload() -> None:
    """固定输入的签名必须等于手工拼出的 payload 的 md5。"""
    expected = hashlib.md5(
        (
            "[151148348][100000002][1601174638012256][f7a808970399e8eb76e274ce7b0bf1ca]"
            f"[100000][{VIDEO_ENC_SALT}][589000][0_589]"
        ).encode()
    ).hexdigest()

    actual = video_log_enc(
        clazz_id="151148348",
        user_id="100000002",
        job_id="1601174638012256",
        object_id="f7a808970399e8eb76e274ce7b0bf1ca",
        playing_time=100,
        duration=589,
    )

    assert actual == expected


def test_seconds_are_converted_to_milliseconds() -> None:
    """秒 → 毫秒换算是最容易漏的一步（漏了签名就全错）。"""
    base = {
        "clazz_id": "1",
        "user_id": "2",
        "job_id": "3",
        "object_id": "4",
        "duration": 589,
    }

    at_one_second = video_log_enc(**base, playing_time=1)  # type: ignore[arg-type]
    at_two_seconds = video_log_enc(**base, playing_time=2)  # type: ignore[arg-type]

    assert at_one_second != at_two_seconds
    # 与手工构造的 1000 毫秒一致
    assert (
        at_one_second
        == hashlib.md5(f"[1][2][3][4][1000][{VIDEO_ENC_SALT}][589000][0_589]".encode()).hexdigest()
    )


def test_subsecond_precision_is_kept_in_signature() -> None:
    """签名按**毫秒**参与计算：10.9 秒 → 10900 毫秒（与 legacy 实现口径一致）。"""
    value = video_log_enc(
        clazz_id="1", user_id="2", job_id="3", object_id="4", playing_time=10.9, duration=100
    )

    assert (
        value
        == hashlib.md5(f"[1][2][3][4][10900][{VIDEO_ENC_SALT}][100000][0_100]".encode()).hexdigest()
    )


def test_player_referer_keeps_version_string() -> None:
    """Referer 必须带**新的**版本号 —— 旧版本号会被服务端 403。"""
    assert "?v=" in VIDEO_PLAYER_REFERER
    assert VIDEO_PLAYER_REFERER != AUDIO_PLAYER_REFERER
    assert "audio" in AUDIO_PLAYER_REFERER


def test_now_ms_looks_like_milliseconds() -> None:
    """``_t`` 是毫秒时间戳（13 位、与当前时间同量级）。"""
    value = now_ms()

    assert value.isdigit()
    assert len(value) == 13
    assert abs(int(value) - int(time.time() * 1000)) < 5000
