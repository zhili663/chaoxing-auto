"""端点注册表的完整性约束。

这些是"改版时不会静默走错地方"的保障：注册名唯一、URL 模板可渲染、
置信度标注齐全（诚实性开关 —— 未校准的端点必须能被 ``cx dev probe`` 找出来）。
"""

from __future__ import annotations

import pytest

from chaoxing.client import endpoints as ep


def test_registry_is_not_empty_and_names_are_unique() -> None:
    """注册名唯一且非空（名字重复会让 "取到哪个端点" 变得不确定）。"""
    names = [spec.name for spec in ep.REGISTRY.values()]

    assert names, "端点注册表为空"
    assert len(names) == len(set(names))
    assert all(name.strip() for name in names)


def test_registry_lookup_rejects_typos() -> None:
    """取不存在的端点必须显式报错，而不是静默返回 None。"""
    with pytest.raises(KeyError, match="未注册的端点"):
        ep.get("not_a_real_endpoint")


def test_every_endpoint_is_absolute_https() -> None:
    """所有端点都必须是 https 绝对地址（避免相对路径拼错到别的域名上）。"""
    for spec in ep.REGISTRY.values():
        assert spec.url.startswith("https://"), spec.name


def test_url_templates_render_with_path_params() -> None:
    """带路径参数的端点要能被渲染。"""
    heartbeat = ep.get("video_heartbeat")
    rendered = heartbeat.url_for(cpi="100000001", dtoken="abc123")

    assert rendered.endswith("/a/100000001/abc123")
    assert "{" not in rendered


def test_endpoints_with_path_params_declare_required_params() -> None:
    """凡 URL 里有占位符的端点，都必须声明 required_params（否则漏参只能线上炸）。"""
    for spec in ep.REGISTRY.values():
        if "{" in spec.url:
            assert spec.required_params, f"{spec.name} 有路径参数却没声明 required_params"


def test_verified_endpoints_carry_evidence_in_note() -> None:
    """VERIFIED 的端点必须写明依据（哪次实测确认的）—— 这是可信度的来源。"""
    for spec in ep.REGISTRY.values():
        if spec.calibration is ep.Calibration.VERIFIED:
            assert spec.note.strip(), f"{spec.name} 标记为 VERIFIED 却没有说明"


def test_calibration_report_shape_is_stable() -> None:
    """校准报告的形状（CLI / API / 覆盖文档都消费它，不能随便改键名）。"""
    report = ep.calibration_report()

    assert len(report) == len(ep.REGISTRY)
    assert {"name", "method", "url", "calibration", "required_params", "note"} <= set(report[0])


def test_volatile_endpoints_are_not_silently_usable() -> None:
    """VOLATILE 端点必须被显式标注出来（默认不启用，改版排查时先看它们）。"""
    volatile = [
        spec for spec in ep.REGISTRY.values() if spec.calibration is ep.Calibration.VOLATILE
    ]

    assert all(spec.note for spec in volatile)
