"""代理池管理：按行导入 / 查看 / 清空。

为什么需要（实测 2026-10-15）：跑批数小时后账号在 ``mooc-ans/*`` 上被 403，
换出口 IP 即恢复 —— 所以池子要能**一行一条**地吃下供应商给的清单。

用法::

    cx proxy import proxies.txt      # 从文件导入
    cx proxy import -                # 从 stdin 导入（cat proxies.txt | cx proxy import -）
    cx proxy list                    # 看有哪些出口（不显示口令）
    cx proxy clear                   # 清空

导入文本支持的写法见 :mod:`chaoxing.client.proxy`（``host:port`` / ``host:port:user:pass`` /
``user:pass@host:port`` / ``http(s)://…`` / ``socks5://…``），空行与 ``#`` 注释自动忽略。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click

from chaoxing.client.proxy import ProxyEntry, ProxyPool, parse_line
from chaoxing.client.proxy_source import ProxyPoolClient
from chaoxing.config import get_settings

#: 池文件路径（放在数据目录下，跟库、日志在一起）
POOL_FILENAME = "proxies.txt"


def pool_path() -> Path:
    """代理池文件的绝对路径。"""
    return Path(get_settings().app.data_dir) / POOL_FILENAME


def load_pool() -> ProxyPool:
    """读取当前代理池（文件不存在即空池 —— 空池 = 全直连）。"""
    path = pool_path()
    if not path.exists():
        return ProxyPool()
    return ProxyPool.from_text(path.read_text(encoding="utf-8"))


def _read_source(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    path = Path(source).expanduser()
    if not path.exists():
        raise click.ClickException(f"文件不存在：{path}")
    return path.read_text(encoding="utf-8", errors="replace")


@click.group(name="proxy")
def proxy() -> None:
    """代理池：按行导入出口，跑批按需轮换以绕过账号级风控。"""


@proxy.command(name="import")
@click.argument("source", type=str)
@click.option("--replace", is_flag=True, help="覆盖式导入（默认是追加 + 去重）")
def import_proxies(source: str, replace: bool) -> None:
    """按行导入代理：SOURCE 是文件路径，或 ``-`` 表示从 stdin 读。

    支持 ``host:port`` / ``host:port:user:pass`` / ``user:pass@host:port`` /
    ``http://user:pass@host:port`` / ``socks5://…``；空行与 ``#`` 注释自动跳过。
    """
    raw = _read_source(source)

    existing = [] if replace else load_pool().entries
    seen = {entry.url for entry in existing}
    merged = list(existing)
    added = 0
    skipped = 0

    for line in raw.splitlines():
        entry = parse_line(line)
        if entry is None:
            if line.strip() and not line.strip().startswith("#"):
                skipped += 1
            continue
        if entry.url in seen:
            continue
        seen.add(entry.url)
        merged.append(entry)
        added += 1

    path = pool_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(entry.url for entry in merged) + ("\n" if merged else ""), "utf-8")

    parts = [f"新增 {added} 条", f"现有 {len(merged)} 条"]
    if skipped:
        parts.append(f"跳过无法识别的 {skipped} 行")
    click.echo(" · ".join(parts) + f"（{path}）")


@proxy.command(name="list")
def list_proxies() -> None:
    """列出池里的出口（只显示 host:port，不显示口令）。"""
    pool = load_pool()
    if not pool.enabled:
        click.echo(f"代理池为空（{pool_path()}）—— 当前全走直连")
        return
    click.echo(f"代理池：{len(pool.entries)} 条（{pool_path()}）")
    for entry in pool.entries:
        extra = f"  [{entry.scheme}]" if entry.scheme != "http" else ""
        click.echo(f"  {entry.label}{extra}")


@proxy.command(name="clear")
@click.confirmation_option(prompt="确认清空代理池？")
def clear_proxies() -> None:
    """清空代理池（回到全直连）。"""
    path = pool_path()
    if path.exists():
        path.write_text("", encoding="utf-8")
    click.echo("代理池已清空")


@proxy.command(name="remote")
@click.option(
    "--url",
    default="",
    help="proxy_pool API 地址；留空取 CHAOXING__PROXY_POOL_API",
)
@click.option("--save", is_flag=True, help="把远端快照写入 data/proxies.txt（离线兜底）")
def remote_pool(url: str, save: bool) -> None:
    """查看 proxy_pool sidecar 当前可用的 HTTPS 出口。

    只读操作：不会把出口写进业务池，除非显式 ``--save``。
    """
    settings = get_settings()
    target = url or settings.chaoxing.proxy_pool_api
    if not target:
        raise click.ClickException(
            "未配置 proxy_pool：设置 CHAOXING__PROXY_POOL_API=http://127.0.0.1:5010"
        )

    async def _run() -> tuple[dict[str, object], list[ProxyEntry]]:
        client = ProxyPoolClient(target, timeout=settings.chaoxing.proxy_pool_timeout)
        try:
            count: dict[str, object] = await client.count()
            entries = await client.fetch_all()
        finally:
            await client.aclose()
        return count, entries

    count, entries = asyncio.run(_run())
    click.echo(
        f"proxy_pool：{target} · 远端 {len(entries)} 条 HTTPS 出口"
        + (f" · count={count.get('count')}" if count else "")
    )
    for entry in entries[:10]:
        click.echo(f"  {entry.label}")
    if len(entries) > 10:
        click.echo(f"  … 其余 {len(entries) - 10} 条")
    if save:
        path = pool_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(entry.url for entry in entries) + ("\n" if entries else ""),
            encoding="utf-8",
        )
        click.echo(f"已写入 {path}")
