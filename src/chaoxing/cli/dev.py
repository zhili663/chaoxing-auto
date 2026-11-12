"""``cx dev``：开发者子命令（协议校准与抓包工具链）。

与运维命令隔离 —— 这些面向"协议维护"，不是日常运行所需（``docs/REFACTOR_PLAN.md`` §5.9）::

    cx dev probe endpoints [--calibration all|verified|template|volatile]
    cx dev probe coverage  [--recorded FILE] [--write]
    cx dev probe heartbeat --account N (--object-id OBJ | --point ID)
    cx dev capture browser --url URL [--out FILE] [--seconds N]
    cx dev capture sanitize -r FILE [-r FILE ...] --out PATH
    cx dev capture diff    --recorded FILE
    cx dev capture proxy

定位说明：

* **浏览器能力由 ``orca-cli`` 提供** —— ``capture browser`` 内部驱动 orca
  （``network har start`` 先于导航，HAR 路径以 ``network har stop`` 的返回值为准）；
  本仓库不自建浏览器驱动，也不依赖 ``playwright``；
* ``capture`` 只做"录制 + 对账"，**不自带中间人代理**（那属于重复造轮子，
  用 mitmproxy / Charles 导出 HAR 再喂给 ``capture diff`` 即可）。
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse

import click
from rich.console import Console
from rich.table import Table

from chaoxing.cli._runtime import run_with_container
from chaoxing.cli.orca_cli import OrcaUnavailable, orca_result, run_orca
from chaoxing.client import VideoApi
from chaoxing.client import endpoints as ep
from chaoxing.client.coverage import (
    build_markdown,
    build_rows,
    load_recorded_urls,
    sanitize_urls,
    summarise,
    unmatched_requests,
)
from chaoxing.client.model.video import ActivationProbe, heartbeat_prerequisites
from chaoxing.config import BASE_DIR
from chaoxing.core.container import Container

CONSOLE = Console()
FIXTURES_DIR = BASE_DIR / "tests" / "fixtures"
COVERAGE_DOC = BASE_DIR / "docs" / "PROTOCOL_COVERAGE.md"
DEFAULT_CAPTURE = BASE_DIR / "data" / "capture" / "session.json"


@click.group()
def dev() -> None:
    """开发者子命令：协议校准与抓包。"""


@dev.group()
def probe() -> None:
    """查看协议注册表与覆盖情况。"""


@probe.command("endpoints")
@click.option(
    "--calibration",
    type=click.Choice(["all", "verified", "template", "volatile"]),
    default="all",
    show_default=True,
    help="只看某一档置信度的端点",
)
def probe_endpoints(calibration: str) -> None:
    """列出端点注册表与校准状态。"""
    specs = [
        spec
        for spec in ep.REGISTRY.values()
        if calibration == "all" or str(spec.calibration) == calibration
    ]
    table = Table(title=f"端点注册表（{calibration}）")
    table.add_column("端点", style="bold", no_wrap=True)
    table.add_column("方法", width=6, no_wrap=True)
    table.add_column("路径", overflow="fold")
    table.add_column("置信度", width=9, no_wrap=True)
    table.add_column("必填参数", overflow="fold")
    for spec in specs:
        table.add_row(
            spec.name,
            spec.method,
            urlparse(spec.url).path,
            str(spec.calibration),
            ", ".join(spec.required_params) or "—",
        )
    CONSOLE.print(table)
    CONSOLE.print(f"共 {len(specs)} 个端点（注册表总计 {len(ep.REGISTRY)} 个）")


@probe.command("coverage")
@click.option(
    "--recorded",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="录制文件（本工具 JSON 或 HAR），用于找出未建模调用",
)
@click.option("--write", is_flag=True, help="写入 docs/PROTOCOL_COVERAGE.md")
def probe_coverage(recorded: Path | None, write: bool) -> None:
    """生成协议覆盖清单（端点 × 测试样本 × 未建模调用）。"""
    urls = load_recorded_urls(recorded) if recorded is not None else []
    rows = build_rows(FIXTURES_DIR)
    stats = summarise(rows)
    unmatched = unmatched_requests(urls)
    markdown = build_markdown(rows, unmatched=unmatched, recorded_count=len(urls))

    if write:
        COVERAGE_DOC.parent.mkdir(parents=True, exist_ok=True)
        COVERAGE_DOC.write_text(markdown, encoding="utf-8", newline="\n")
        CONSOLE.print(f"[green]已写入[/]{COVERAGE_DOC}")
    else:
        CONSOLE.print(markdown)

    CONSOLE.print(
        f"端点 {stats['total']} 个（VERIFIED {stats['verified']}）· "
        f"已有样本 {stats['with_fixture']} 个 · 未建模调用 {len(unmatched)} 条"
    )


def describe_probe(probe: ActivationProbe, missing: tuple[str, ...]) -> list[str]:
    """把探测结果翻成给人看的几行。

    **两个 dtoken 都要露出**，并给出适用性说明 —— 会话此前已经打开过播放器页时，
    前后 dtoken 可能相同，那不代表视频未激活。

    :param probe: 探测结果
    :param missing: 卡片里缺失的心跳必需字段
    :returns: 逐行文本
    """
    if probe.changed:
        verdict = "已激活：dtoken 随播放器页发生了变化，可以正常上报心跳"
    else:
        verdict = "未激活：两次 dtoken 相同 —— 这个视频很可能从未被真实播放过（详见适用性说明）"

    lines = [
        f"视频 objectId：{probe.object_id}",
        f"打开播放器页前的 dtoken：{probe.dtoken_before or '（未签发）'}",
        f"打开播放器页后的 dtoken：{probe.dtoken_after or '（未签发）'}",
        f"结论：{verdict}",
        "适用性：若本次会话此前已打开过播放器页，前后 dtoken 可能相同 —— 那不代表视频未激活",
    ]
    if not probe.changed:
        lines.append("建议动作：在浏览器里把这个视频播几秒，再重跑本命令")
    if missing:
        lines.append(f"卡片缺字段：{', '.join(missing)} —— 缺一个就可能 403，建议先重扫任务点")
    return lines


@probe.command("heartbeat")
@click.option("--account", "account_id", type=int, required=True, help="账号 ID")
@click.option("--object-id", "object_id", default=None, help="视频 objectId（只做激活探测）")
@click.option("--point", "point_id", type=int, default=None, help="任务点 ID（追加卡片字段检查）")
def probe_heartbeat(account_id: int, object_id: str | None, point_id: int | None) -> None:
    """探测某个视频的心跳资格：dtoken 是否已激活 + 卡片字段是否齐全。"""
    if not object_id and point_id is None:
        CONSOLE.print("[red]需要 --object-id 或 --point[/]")
        raise SystemExit(2)

    async def action(container: Container) -> tuple[ActivationProbe, tuple[str, ...]]:
        target = object_id or ""
        missing: tuple[str, ...] = ()
        if point_id is not None:
            point = await container.study.get_point(point_id)
            if point is None:
                CONSOLE.print(f"[red]任务点 {point_id} 不存在[/]")
                raise SystemExit(1)
            target = target or point.point_id
            missing = heartbeat_prerequisites(point.payload)
        if not container.registry.has(account_id):
            CONSOLE.print(f"[red]账号 {account_id} 没有登录态[/]，先执行：cx auth login")
            raise SystemExit(1)
        probe = await VideoApi(container.transport).probe_activation(
            container.registry.get(account_id), object_id=target
        )
        return probe, missing

    probe, missing = run_with_container(action)
    for line in describe_probe(probe, missing):
        CONSOLE.print(line)


@dev.group()
def capture() -> None:
    """抓包工具链：录制真实请求 → 与端点注册表对账。"""


@capture.command("browser")
@click.option("--url", required=True, help="要打开并录制的页面")
@click.option(
    "--out",
    type=click.Path(dir_okay=False, path_type=Path),
    default=DEFAULT_CAPTURE,
    show_default=True,
    help="录制结果保存路径（工具格式；同名 .har 留在同目录）",
)
@click.option(
    "--seconds", type=float, default=30.0, show_default=True, help="导航后的等待时长（秒）"
)
def capture_browser(url: str, out: Path, seconds: float) -> None:
    """用 orca-cli 打开页面并录制网络请求（HAR → 本工具格式）。

    **先开 HAR 再导航** —— 反过来一条都录不到（实测 ``requestCount: 0``）。
    HAR 的落盘路径由 ``network har stop`` 返回，不由我们指定（见 ``cli/orca_cli.py``）。
    """
    try:
        page_id = str(orca_result(run_orca("tab", "create", "--json")).get("browserPageId", ""))
        orca_result(run_orca("exec", "--command", "network har start", "--json"))
        orca_result(run_orca("goto", "--url", url, "--json"))
        orca_result(run_orca("wait", "--load", "networkidle", "--json"))
        time.sleep(max(seconds, 0.0))
        stopped = orca_result(run_orca("exec", "--command", "network har stop", "--json"))
    except OrcaUnavailable as exc:
        CONSOLE.print(f"[red]录制失败[/]{exc}")
        raise SystemExit(1) from exc

    har_path = Path(str(stopped.get("path", "")))
    urls = load_recorded_urls(har_path)
    if not urls:
        CONSOLE.print(f"[yellow]没有录到请求[/]（HAR：{har_path}）")
        raise SystemExit(1)

    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(har_path, out.with_suffix(".har"))
    out.write_text(
        json.dumps(
            {"url": url, "requests": [{"url": item} for item in urls]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    CONSOLE.print(f"[green]已录制[/]{out}（{len(urls)} 条请求 · 页面 {page_id}）")
    CONSOLE.print(f"接着对账：cx dev capture diff --recorded {out}")


@capture.command("diff")
@click.option(
    "--recorded",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="录制文件（本工具 JSON 或 HAR）",
)
def capture_diff(recorded: Path) -> None:
    """把录制流量与端点注册表对账，列出**未建模调用**。"""
    urls = load_recorded_urls(recorded)
    if not urls:
        CONSOLE.print("[yellow]录制文件里没有可识别的请求[/]")
        raise SystemExit(1)

    unmatched = unmatched_requests(urls)
    CONSOLE.print(f"录制请求 {len(urls)} 条，其中未建模 {len(unmatched)} 条")
    if not unmatched:
        CONSOLE.print("[green]全部命中已注册端点[/]")
        return
    for url in unmatched:
        CONSOLE.print(f"  [yellow]?[/] {url}")
    raise SystemExit(1)


@capture.command("proxy")
def capture_proxy() -> None:
    """代理式抓包的接入方式（本项目不自带 MITM 代理）。"""
    CONSOLE.print("代理式抓包请用现成工具，本项目只做对账：")
    CONSOLE.print("  1) 用 mitmproxy / Charles 抓取，导出 HAR；")
    CONSOLE.print("  2) cx dev capture diff --recorded <导出的 har>")
    CONSOLE.print("  3) 缺样本的端点用 cx dev capture browser 补录页面")


@capture.command("sanitize")
@click.option(
    "--recorded",
    "-r",
    "recorded",
    required=True,
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="录制文件（可多次；接受本工具 JSON / HAR / 调试 JSON）",
)
@click.option(
    "--out",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="脱敏后的 URL 清单输出路径",
)
def capture_sanitize(recorded: tuple[Path, ...], out: Path) -> None:
    """把抓到的流量脱敏成可入库的 URL 清单（覆盖对账与回归用）。"""
    urls: list[str] = []
    for path in recorded:
        urls.extend(load_recorded_urls(path))

    sanitized = sanitize_urls(urls)
    if not sanitized:
        CONSOLE.print("[yellow]录制文件里没有可识别的请求[/]")
        raise SystemExit(1)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"url": str(recorded[0]), "requests": [{"url": url} for url in sanitized]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    CONSOLE.print(f"[green]已写入[/]{out}（{len(sanitized)} 条 URL）")
