"""协议覆盖对账的回归。

对应 ``docs/REFACTOR_PLAN.md`` §5.14 的要求：**未建模调用是一等公民** ——
对账必须能把"真实录制里出现、但注册表里没有"的调用挑出来。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from chaoxing.client import endpoints as ep
from chaoxing.client.coverage import (
    build_markdown,
    build_rows,
    harvest_urls,
    load_recorded_urls,
    match_fixtures,
    sanitize_url,
    summarise,
    unmatched_requests,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_fixture_matching_finds_course_list_sample() -> None:
    """课程列表端点应当匹配到 course_list.html。"""
    mapping = match_fixtures(ep.REGISTRY.values(), FIXTURES)

    assert mapping.get("course_list") == "course_list.html"


def test_fixture_matching_survives_missing_directory() -> None:
    """样本目录不存在时不能崩（CI 或精简部署里可能没有 fixtures）。"""
    assert match_fixtures(ep.REGISTRY.values(), None) == {}
    assert match_fixtures(ep.REGISTRY.values(), Path("does-not-exist")) == {}


def test_placeholder_paths_match_real_requests() -> None:
    """带占位符的端点路径要能匹配真实请求（``{cpi}`` / ``{dtoken}`` 按任意段处理）。"""
    urls = [
        "https://mooc1.chaoxing.com/mooc-ans/multimedia/log/a/507072787/abc123",
        "https://mooc1.chaoxing.com/ananas/status/f7a808970399e8eb76e274ce7b0bf1ca",
    ]

    assert unmatched_requests(urls) == []


def test_known_path_with_query_is_matched() -> None:
    """查询串不影响路径匹配。"""
    urls = ["https://mooc1.chaoxing.com/visit/courselistdata?courseType=1&courseFolderId=0"]

    assert unmatched_requests(urls) == []


def test_unknown_chaoxing_call_is_reported() -> None:
    """注册表里没有的学习通调用必须被列出来。"""
    urls = ["https://mooc1.chaoxing.com/brand/new/api/thing?x=1"]

    assert unmatched_requests(urls) == urls


def test_non_chaoxing_hosts_are_ignored() -> None:
    """第三方 CDN 与统计脚本不属于本项目协议面。"""
    urls = [
        "https://cdn.example.com/static/app.js",
        "https://www.google-analytics.com/collect?v=1",
    ]

    assert unmatched_requests(urls) == []


def test_duplicates_are_reported_once() -> None:
    """同一个未建模 URL 重复出现只报一次。"""
    url = "https://mooc1.chaoxing.com/brand/new/api/thing"

    assert unmatched_requests([url, url]) == [url]


def test_summarise_counts_by_calibration() -> None:
    """统计要能对上注册表的真实分布。"""
    stats = summarise(build_rows(FIXTURES))

    assert stats["total"] == len(ep.REGISTRY)
    assert stats["verified"] + stats["template"] + stats["volatile"] == stats["total"]
    assert stats["with_fixture"] >= 1


def test_markdown_lists_unmatched_requests() -> None:
    """报告里必须出现未建模调用（否则这份文档没有说服力）。"""
    markdown = build_markdown(
        build_rows(FIXTURES),
        unmatched=["https://mooc1.chaoxing.com/brand/new/api"],
        recorded_count=3,
    )

    assert "未建模调用" in markdown
    assert "brand/new/api" in markdown
    assert "`course_list`" in markdown


def test_markdown_reports_none_when_all_matched() -> None:
    """全部命中时要明确写"未发现"，而不是留空。"""
    markdown = build_markdown(build_rows(FIXTURES))

    assert "（未发现）" in markdown


# --------------------------------------------------------------------------- #
# 录制脱敏（覆盖对账的素材管线）
# --------------------------------------------------------------------------- #


def test_harvest_urls_walks_unknown_shapes() -> None:
    """临时调试 json 的形态五花八门（见 data/debug/*.json），抽 URL 要递归且不崩。"""
    payload = {
        "captured": [{"url": "https://mooc1.chaoxing.com/visit/courselistdata"}],
        "nested": {"x": {"y": "https://mooc1.chaoxing.com/mooc-ans/api/work"}},
        "text": "不是 URL",
        "num": 42,
    }

    assert harvest_urls(payload) == [
        "https://mooc1.chaoxing.com/visit/courselistdata",
        "https://mooc1.chaoxing.com/mooc-ans/api/work",
    ]
    assert harvest_urls(42) == []


def test_sanitize_url_drops_query_and_masks_identifiers() -> None:
    """入库的录制只剩形状：查询串整条丢弃，长 ID/token 段替换成 {id}。"""
    url = (
        "https://mooc1.chaoxing.com/mooc-ans/multimedia/log/a/581371075/"
        "9eb1bcaeede460b220c37412950e77bc?clazzId=155339185&otherInfo=nodeId_1214035150"
    )

    assert sanitize_url(url) == ("https://mooc1.chaoxing.com/mooc-ans/multimedia/log/a/{id}/{id}")


def test_sanitize_url_keeps_literal_path_segments() -> None:
    """纯字母的长路径段是接口名，不能被当成 ID 抹掉 —— 抹了对账会全盘失败。"""
    assert (
        sanitize_url("https://mooc1.chaoxing.com/mooc-ans/mycourse/studentstudycourselist")
        == "https://mooc1.chaoxing.com/mooc-ans/mycourse/studentstudycourselist"
    )
    assert sanitize_url("https://mooc1.chaoxing.com/mooc-ans/work/addStudentWorkNewWeb").endswith(
        "/mooc-ans/work/addStudentWorkNewWeb"
    )


def test_load_recorded_urls_falls_back_to_harvest(tmp_path: Path) -> None:
    """对账入口接受三种形态：本工具 JSON / HAR / 任意嵌套的调试 JSON。"""
    path = tmp_path / "capture.json"
    path.write_text(
        json.dumps({"captured": [{"url": "https://mooc1.chaoxing.com/visit/courselistdata"}]}),
        encoding="utf-8",
    )

    assert load_recorded_urls(path) == ["https://mooc1.chaoxing.com/visit/courselistdata"]


# --------------------------------------------------------------------------- #
# 覆盖守卫（脱敏后的真机录制 + 忽略规则 + 报告渲染）
# --------------------------------------------------------------------------- #

#: 脱敏后的真实录制（由 `cx dev capture sanitize` 生成，见 Task 1）
FIXTURE_RECORDING = (
    Path(__file__).resolve().parents[1] / "fixtures" / "recorded" / "course_session_urls.json"
)


def test_recorded_session_is_fully_accounted_for() -> None:
    """真机录制里出现的每一条学习通调用，要么有端点，要么在规则表里带原因。"""
    urls = load_recorded_urls(FIXTURE_RECORDING)

    assert urls, "录制 fixture 抽不出 URL —— 对账会退化成空转"
    assert unmatched_requests(urls) == []


def test_registered_endpoints_are_not_swallowed_by_ignore_rules() -> None:
    """注册表里的每个端点都必须能通过守卫（防止忽略规则把真接口吃掉）。"""
    urls = []
    for spec in ep.REGISTRY.values():
        placeholders = dict.fromkeys(re.findall(r"\{(\w+)\}", spec.url), "zz1")
        urls.append(spec.url_for(**placeholders))

    assert unmatched_requests(urls) == []


def test_markdown_lists_ignored_rules_with_reasons() -> None:
    """范围外规则必须出现在报告里 —— 静默跳过等于没有对账。"""
    markdown = build_markdown(build_rows(FIXTURES))

    assert "mobilelearn.chaoxing.com" in markdown
    assert "范围外" in markdown


def test_markdown_lists_unmodeled_faces() -> None:
    """能力级缺口（讨论 / 考试 / 人机验证）要写进「未还原原因」表。"""
    markdown = build_markdown(build_rows(FIXTURES))

    assert "discussion" in markdown
    assert "exam" in markdown


def test_markdown_documents_the_orca_capture_flow() -> None:
    """抓包流程必须是 orca-cli 版 —— 本仓库不再自建浏览器驱动（计划 Task 7）。"""
    markdown = build_markdown(build_rows(FIXTURES))

    assert 'orca exec --command "network har start"' in markdown
    assert "capture sanitize" in markdown
