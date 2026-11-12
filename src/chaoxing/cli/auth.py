"""``cx auth``：超星账号的认证与登录态管理。

命令（两级嵌套，见 ``docs/REFACTOR_PLAN.md`` §5.9）::

    cx auth login  -u 手机号 [-p 密码] [--captcha 验证码] [--account-id N] [--encoding M]
    cx auth logout --account-id N
    cx auth refresh
    cx auth stats

**会话存在数据库里**（``accounts.cookie_enc``，Fernet 加密），与 ``cx course`` / ``cx study``
用的是同一套 ``SessionRegistry``。密码只在进程内使用，既不入库也不落盘；
若服务端索要验证码，会把图片存到 ``data/captcha/`` 并打印路径，人工识别后用 ``--captcha`` 重试。
"""

from __future__ import annotations

import time
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from chaoxing.cli._runtime import run_with_container
from chaoxing.client import LoginApi, LoginResult, SessionState
from chaoxing.config import Settings
from chaoxing.core.container import Container

CONSOLE = Console()


def _save_captcha(settings: Settings, image: bytes) -> Path:
    """把验证码图片落到 ``data/captcha/``，返回路径。"""
    directory = settings.app.data_dir / "captcha"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{int(time.time())}.jpg"
    path.write_bytes(image)
    return path


async def _login(
    container: Container,
    account_id: int,
    username: str,
    password: str,
    captcha: str,
    encoding: str,
) -> tuple[LoginResult, Path | None]:
    """执行登录；命中验证码时顺带把图片取回来。"""
    api = LoginApi(container.transport)
    async with container.registry.acquire(account_id) as session:
        result = await api.login(session, username, password, captcha=captcha, encoding=encoding)
        captcha_path: Path | None = None
        if not result.ok and result.reason == "captcha":
            captcha_path = _save_captcha(container.settings, await api.fetch_captcha(session))
        if result.ok:
            # 落库（加密）—— 否则下一个命令是另一个进程，会找不到登录态
            await container.registry.persist(account_id)
            async with container.uow.begin() as db:
                from chaoxing.storage.models import Account

                account = await db.get(Account, account_id)
                if account is not None:
                    account.username = username
                    account.status = "logged_in"
        return result, captcha_path


async def _refresh(container: Container) -> list[tuple[int, str, bool]]:
    """重新校验全部已保存会话的登录态。"""
    api = LoginApi(container.transport)
    results: list[tuple[int, str, bool]] = []
    for state in container.registry.all():
        if not state.cookies:
            results.append((state.account_id, state.username, False))
            continue
        ok = await api.verify_session(state)
        state.status = "logged_in" if ok else "expired"
        state.dirty = True
        await container.registry.persist(state.account_id)
        results.append((state.account_id, state.username, ok))
    return results


def _rows(states: list[SessionState]) -> list[tuple[str, str, str, str, str]]:
    """会话 → 表格行。"""
    return [
        (
            str(state.account_id),
            state.username or "-",
            state.status,
            str(len(state.cookies)),
            str(len(state.tokens)),
        )
        for state in states
    ]


@click.group()
def auth() -> None:
    """超星账号：认证与登录态管理。"""


@auth.command()
@click.option("--username", "-u", required=True, help="手机号 / 用户名")
@click.option("--password", "-p", default="", help="密码；留空则交互式输入（不回显）")
@click.option("--captcha", default="", help="图形验证码（服务端索要时填）")
@click.option("--account-id", type=int, default=1, show_default=True, help="本地账号 ID")
@click.option(
    "--encoding",
    type=click.Choice(["plain", "base64", "md5"]),
    default="plain",
    show_default=True,
    help="密码编码方式（确定密码正确却失败时依次切换）",
)
def login(username: str, password: str, captcha: str, account_id: int, encoding: str) -> None:
    """登录超星账号并把会话保存到本地（加密入库）。"""
    if not password:
        password = click.prompt("密码", hide_input=True)

    async def action(container: Container) -> tuple[LoginResult, Path | None]:
        return await _login(container, account_id, username, password, captcha, encoding)

    result, captcha_path = run_with_container(action)

    if result.ok:
        CONSOLE.print(
            f"[green]登录成功[/]账号 {account_id}（{username}）uid={result.uid or '未知'}"
        )
    else:
        CONSOLE.print(f"[red]登录失败[/]{result.message} [dim]({result.reason})[/]")
        if captcha_path is not None:
            CONSOLE.print(f"验证码图片已保存：[bold]{captcha_path}[/]")
            CONSOLE.print("看清后重试：cx auth login -u <手机号> --captcha <验证码>")
    raise SystemExit(0 if result.ok else 1)


@auth.command()
@click.option("--account-id", type=int, required=True, help="本地账号 ID")
def logout(account_id: int) -> None:
    """删除本地保存的会话（账号实体保留）。"""

    async def action(container: Container) -> bool:
        if not container.registry.has(account_id):
            return False
        container.registry.remove(account_id)
        await container.registry.persist(account_id)
        async with container.uow.begin() as db:
            from chaoxing.storage.models import Account

            account = await db.get(Account, account_id)
            if account is not None:
                account.status = "pending"
        return True

    if run_with_container(action):
        CONSOLE.print(f"[green]已删除[/]账号 {account_id} 的本地会话")
        return
    CONSOLE.print(f"[yellow]账号 {account_id} 没有已保存的会话[/]")
    raise SystemExit(1)


@auth.command()
def refresh() -> None:
    """重新校验所有已保存会话的登录态。"""

    async def action(container: Container) -> list[tuple[int, str, bool]]:
        return await _refresh(container)

    results = run_with_container(action)
    if not results:
        CONSOLE.print("[yellow]没有已保存的会话[/]先用 `cx auth login` 登录")
        return

    table = Table(title="会话校验结果")
    table.add_column("账号 ID", justify="right")
    table.add_column("用户名")
    table.add_column("状态")
    alive = 0
    for account_id, username, ok in results:
        alive += int(ok)
        table.add_row(
            str(account_id),
            username or "-",
            "[green]有效[/]" if ok else "[red]需重新登录[/]",
        )
    CONSOLE.print(table)
    raise SystemExit(0 if alive == len(results) else 1)


@auth.command()
def stats() -> None:
    """查看本地会话总览。"""

    async def action(container: Container) -> list[tuple[str, str, str, str, str]]:
        return _rows(list(container.registry.all()))

    rows = run_with_container(action)
    if not rows:
        CONSOLE.print("[yellow]没有已保存的会话[/]")
        return

    table = Table(title="本地会话（数据库）")
    table.add_column("账号 ID", justify="right")
    table.add_column("用户名")
    table.add_column("状态")
    table.add_column("Cookie", justify="right")
    table.add_column("令牌", justify="right")
    for row in rows:
        table.add_row(*row)
    CONSOLE.print(table)
