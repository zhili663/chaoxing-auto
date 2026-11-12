"""``cx account``：超星账号实体（**不是登录态** —— 那是 ``cx auth``）。

    cx account list                    列出账号
    cx account add -u 手机号 [-p 密码]  新增账号
    cx account enable  ID / disable ID 启用 / 停用
    cx account remove  ID [--yes]      删除（课程与任务点级联清理）

分工说清楚：``cx auth`` 管"登录态"（会话），``cx account`` 管"账号实体"。
课程与任务点挂在实体下（外键约束），所以**先有实体、再谈登录** ——
只跑 `cx auth login` 而不建实体，后续扫课会因外键失败。
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from chaoxing.cli._runtime import run_with_container
from chaoxing.core.container import Container
from chaoxing.core.services.accounts import AccountExists
from chaoxing.storage.models import Account

CONSOLE = Console()


@click.group()
def account() -> None:
    """超星账号实体：列表 / 新增 / 启停 / 删除。"""


@account.command("list")
def list_accounts() -> None:
    """列出账号（含登录态与启停状态）。"""

    async def action(container: Container) -> list[Account]:
        return await container.accounts.list_accounts()

    accounts = run_with_container(action)
    if not accounts:
        CONSOLE.print("[yellow]还没有账号[/]用 `cx account add -u <手机号>` 新增")
        return

    table = Table(title=f"超星账号（{len(accounts)}）")
    table.add_column("ID", justify="right")
    table.add_column("账号")
    table.add_column("状态")
    table.add_column("登录态")
    table.add_column("备注")
    for item in accounts:
        table.add_row(
            str(item.id),
            item.username,
            item.status,
            "✓" if item.cookie_enc else "",
            item.note or "",
        )
    CONSOLE.print(table)


@account.command("add")
@click.option("-u", "--username", required=True, help="超星账号（手机号 / 学号）")
@click.option("-p", "--password", default=None, help="密码；留空则交互式输入（不回显）")
@click.option("--note", default="", help="备注（机房场景里比账号好认）")
@click.option("--priority", type=int, default=100, show_default=True, help="数值小的先刷")
def add_account(username: str, password: str | None, note: str, priority: int) -> None:
    """新增账号（密码加密落库）。"""
    secret = password if password is not None else click.prompt("密码", hide_input=True)

    async def action(container: Container) -> Account:
        return await container.accounts.create_account(
            username=username, password=secret, note=note, priority=priority
        )

    try:
        created = run_with_container(action)
    except AccountExists as exc:
        CONSOLE.print(f"[red]已存在[/]{exc}")
        raise SystemExit(1) from exc

    CONSOLE.print(f"[green]已新增[/]账号 {created.id}（{created.username}）")
    CONSOLE.print(f"接着登录：cx auth login -u {username} --account-id {created.id}")


@account.command("enable")
@click.argument("account_id", type=int)
def enable_account(account_id: int) -> None:
    """启用账号。"""

    async def action(container: Container) -> bool:
        return await container.accounts.set_enabled(account_id, enabled=True)

    _report_state(run_with_container(action), account_id, "已启用")


@account.command("disable")
@click.argument("account_id", type=int)
def disable_account(account_id: int) -> None:
    """停用账号（同时清掉本地会话，避免它被"复活"成可用状态）。"""

    async def action(container: Container) -> bool:
        return await container.accounts.set_enabled(account_id, enabled=False)

    _report_state(run_with_container(action), account_id, "已停用")


@account.command("remove")
@click.argument("account_id", type=int)
@click.option("--yes", is_flag=True, help="跳过确认")
def remove_account(account_id: int, yes: bool) -> None:
    """删除账号（关联的课程与任务点会一起清理）。"""
    if not yes:
        click.confirm(f"确认删除账号 {account_id}？它的课程与任务点会一起消失", abort=True)

    async def action(container: Container) -> bool:
        return await container.accounts.delete_account(account_id)

    _report_state(run_with_container(action), account_id, "已删除")


def _report_state(ok: bool, account_id: int, done: str) -> None:
    """统一的成功/失败输出。"""
    if ok:
        CONSOLE.print(f"[green]{done}[/]账号 {account_id}")
    else:
        CONSOLE.print(f"[red]账号 {account_id} 不存在[/]")
        raise SystemExit(1)
