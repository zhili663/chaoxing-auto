"""配置层的真实边界用例。

每一条都对应真会出事的场景，来源标注在用例上：

* ``insecure_exposure``：legacy 的安全闸门，曾真实拦住"无鉴权丢到公网"；
* 心跳区间写反：手改 env 时最典型的手误，跑起来才发现就晚了；
* 嵌套 env：新架构里**唯一**的配置注入路径（业务代码禁止直读 os.environ）；
* legacy 扁平键共存：仓库里还躺着一份旧 ``.env``，不能被误读成新配置。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from chaoxing.config import AppSettings, ChaoxingSettings, Settings


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离环境：不读仓库里的 .env，也不残留跨用例的 env 变量。"""
    for key in list({"APP", "CHAOXING", "AI", "OBS"}):
        for name in ("__HOST", "__PORT", "__DATA_DIR", "__API_TOKEN", "__ALLOW_OPEN_ACCESS"):
            monkeypatch.delenv(f"{key}{name}", raising=False)
    monkeypatch.delenv("CHAOXING__VIDEO_RATE", raising=False)
    monkeypatch.delenv("CHAOXING__HEARTBEAT_INTERVAL_MIN", raising=False)
    monkeypatch.delenv("CHAOXING__HEARTBEAT_INTERVAL_MAX", raising=False)


def _settings(**overrides: object) -> Settings:
    """构造一个不读 .env 的配置对象。"""
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 嵌套 env（新架构唯一的配置入口）
# --------------------------------------------------------------------------- #


def test_nested_env_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """双下划线嵌套键必须能覆盖默认值。"""
    monkeypatch.setenv("CHAOXING__VIDEO_RATE", "1.5")
    monkeypatch.setenv("APP__PORT", "9123")

    settings = _settings()

    assert settings.chaoxing.video_rate == 1.5
    assert settings.app.port == 9123


def test_legacy_flat_keys_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """legacy 的扁平键（VIDEO_RATE）不得影响新的嵌套配置。"""
    monkeypatch.setenv("VIDEO_RATE", "9.9")

    settings = _settings()

    # 扁平键必须被忽略：取值应等于**模型默认**（别把默认值写死 —— 默认会随实测调整）
    from chaoxing.config import ChaoxingSettings

    assert settings.chaoxing.video_rate == ChaoxingSettings().video_rate
    assert settings.chaoxing.video_rate != 9.9


# --------------------------------------------------------------------------- #
# 心跳窗口（写反必须启动即失败）
# --------------------------------------------------------------------------- #


def test_reversed_heartbeat_window_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """MIN > MAX 属于配置事故，必须在构造期就炸，而不是运行期静默变慢。"""
    monkeypatch.setenv("CHAOXING__HEARTBEAT_INTERVAL_MIN", "30")
    monkeypatch.setenv("CHAOXING__HEARTBEAT_INTERVAL_MAX", "10")

    with pytest.raises(ValidationError, match="HEARTBEAT_INTERVAL_MIN"):
        _settings()


def test_default_heartbeat_window_is_ordered() -> None:
    """默认值自身必须合法（防呆：改默认值时手滑）。"""
    chaoxing = ChaoxingSettings()
    assert chaoxing.heartbeat_interval_min <= chaoxing.heartbeat_interval_max


# --------------------------------------------------------------------------- #
# 危险暴露闸门
# --------------------------------------------------------------------------- #


def test_public_listen_without_token_is_insecure() -> None:
    """对外监听 + 无 token = 危险配置（应用会拒绝启动）。"""
    app = AppSettings(host="0.0.0.0", api_token="")

    assert app.listens_publicly is True
    assert app.insecure_exposure is True
    assert "拒绝启动" in app.exposure_help()


def test_loopback_is_never_insecure() -> None:
    """本机回环永远安全（legacy 的"本机开发免鉴权"体验要保留）。"""
    assert AppSettings(host="127.0.0.1", api_token="").insecure_exposure is False


def test_public_listen_with_token_is_fine() -> None:
    """对外监听 + 有 token = 合法部署形态。"""
    assert AppSettings(host="0.0.0.0", api_token="s3cret").insecure_exposure is False


def test_allow_open_access_downgrades_to_accepted_risk() -> None:
    """显式声明接受风险后不再阻断（但仍可被 doctor 提示为 WARN）。"""
    app = AppSettings(host="0.0.0.0", api_token="", allow_open_access=True)

    assert app.listens_publicly is True
    assert app.insecure_exposure is False


def test_settings_surfaces_exposure_through_nested_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """嵌套 env 也要能触发同一个闸门（端到端串一次）。"""
    monkeypatch.setenv("APP__HOST", "0.0.0.0")

    assert _settings().app.insecure_exposure is True


# --------------------------------------------------------------------------- #
# 路径派生
# --------------------------------------------------------------------------- #


def test_paths_derive_from_data_dir() -> None:
    """日志与数据库路径都从 data_dir 派生，避免配置项散落。"""
    settings = _settings(app=AppSettings(data_dir=Path("/tmp/cx-test")))

    assert settings.log_dir == Path("/tmp/cx-test/logs")
    assert settings.db_path == Path("/tmp/cx-test/app.db")


def test_explicit_log_dir_wins() -> None:
    """显式配置的日志目录优先于 data_dir 派生值。"""
    settings = _settings(
        app=AppSettings(data_dir=Path("/tmp/cx-test")),
        obs={"log_dir": Path("/var/log/cx")},
    )

    assert settings.log_dir == Path("/var/log/cx")
