"""配置覆盖：白名单、类型转换、脱敏、嵌套还原。

来源：计划 §5.10 的"零硬编码"—— 配置的最后一层落在数据库，改完即生效、重启不丢。
两条安全约束写进了实现，也写进了这些用例：
① **能改什么由白名单决定**（监听地址/数据库路径/SECRET_KEY 不在其中）；
② 读配置**必须脱敏**（不能把密钥回显给调用方）。
"""

from __future__ import annotations

from chaoxing.config import Settings
from chaoxing.core.services.preferences import (
    ALLOWED_KEYS,
    InvalidValue,
    PreferenceService,
    UnknownSetting,
    coerce,
    mask,
    nest,
)
from chaoxing.storage.uow import UnitOfWork


def make_service(uow: UnitOfWork) -> PreferenceService:
    """构造服务（基础配置用固定值，便于断言默认值）。"""
    base = Settings(_env_file=None, secret_key="A" * 43 + "=")  # type: ignore[call-arg]
    return PreferenceService(uow=uow, base=base)


def test_unknown_key_is_rejected() -> None:
    """白名单外的键一律拒绝（不是默默忽略）。"""
    for key in ("app.host", "app.port", "secret_key", "app.data_dir"):
        try:
            coerce(key, "x")
        except UnknownSetting:
            continue
        raise AssertionError(f"{key} 不该被允许修改")


def test_value_types_are_coerced() -> None:
    """命令行给的都是字符串，要按配置项的类型转换。"""
    assert coerce("chaoxing.max_retry", "3") == 3
    assert coerce("chaoxing.video_rate", "1.5") == 1.5
    assert coerce("ai.model", "gpt-4o-mini") == "gpt-4o-mini"


def test_bad_value_is_reported() -> None:
    """转换失败要说清"需要什么类型"。"""
    try:
        coerce("chaoxing.max_retry", "不是数字")
    except InvalidValue as exc:
        assert "int" in str(exc)
        return
    raise AssertionError("非法值应当报错")


def test_nest_rebuilds_sections() -> None:
    """扁平键要还原成 pydantic 期望的嵌套结构。"""
    nested = nest({"chaoxing.video_rate": 2.0, "ai.model": "x", "invalid": 1})

    assert nested == {"chaoxing": {"video_rate": 2.0}, "ai": {"model": "x"}}


def test_mask_hides_secrets() -> None:
    """敏感项只回显"是否已设置"。"""
    assert mask("secret_key", "abc") == "（已设置）"
    assert mask("app.api_token", "abc") == "（已设置）"
    assert mask("chaoxing.video_rate", 2.0) == 2.0


async def test_set_and_unset_override(uow: UnitOfWork) -> None:
    """写入覆盖 → 生效 → 删除回默认。"""
    service = make_service(uow)

    # 不传 user_id：操作者是可选的（CLI 改配置时通常没有"用户"概念）
    await service.set("chaoxing.video_rate", "1.5")
    described = await service.describe()
    assert described["chaoxing.video_rate"]["overridden"] is True
    assert described["chaoxing.video_rate"]["value"] == 1.5

    assert await service.unset("chaoxing.video_rate") is True
    described_after = await service.describe()
    assert described_after["chaoxing.video_rate"]["overridden"] is False
    assert await service.unset("chaoxing.video_rate") is False


async def test_resolve_applies_overrides(uow: UnitOfWork) -> None:
    """覆盖要真的改变"当前生效"的配置对象。"""
    service = make_service(uow)

    await service.set("chaoxing.video_rate", "1.25")
    resolved = await service.resolve()

    assert resolved.chaoxing.video_rate == 1.25
    # 未覆盖的项保持原值
    assert resolved.chaoxing.max_progress_step == service._base.chaoxing.max_progress_step


async def test_describe_covers_every_allowed_key(uow: UnitOfWork) -> None:
    """设置页要能看到白名单里的每一项。"""
    described = await make_service(uow).describe()

    assert set(described) == set(ALLOWED_KEYS)
    assert all("overridden" in item for item in described.values())


async def test_secret_keys_never_appear_in_describe(uow: UnitOfWork) -> None:
    """读配置时不能把密钥值回显出去。"""
    base = Settings(  # type: ignore[call-arg]
        _env_file=None, secret_key="SUPER-SECRET-KEY", app={"api_token": "TOKEN-VALUE"}
    )
    service = PreferenceService(uow=uow, base=base)

    described = await service.describe()

    assert "SUPER-SECRET-KEY" not in str(described)
    assert "TOKEN-VALUE" not in str(described)
