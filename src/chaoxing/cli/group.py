"""``cx group``：虚拟账户组（批量操作与共享的粒度）。

    cx group list                      列出组与成员数
    cx group create NAME [--public]    建组
    cx group add    GROUP_ID 账号ID...  把账号加进组
    cx group remove GROUP_ID 账号ID...  把账号移出组（不删账号）
    cx group delete GROUP_ID           删组（组里的账号不受影响）

组只是"视图与批量入口"：加入组**不改变账号归属**，可见性仍由账号自己的属主决定。
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from chaoxing.cli._runtime import run_with_container
from chaoxing.core.container import Container
from chaoxing.core.services.groups import GroupExists, GroupNotFound, GroupOverview

CONSOLE = Console()


@click.group()
def group() -> None:
    """虚拟账户组：列表 / 建组 / 增删成员。"""


@group.command("list")
def list_groups() -> None:
    """列出组与成员数。"""

    async def action(container: Container) -> list[GroupOverview]:
        return await container.groups.overview()

    rows = run_with_container(action)
    if not rows:
        CONSOLE.print("[yellow]还没有组[/]用 `cx group create 机房A` 建一个")
        return

    table = Table(title=f"账户组（{len(rows)}）")
    table.add_column("ID", justify="right")
    table.add_column("组名")
    table.add_column("成员", justify="right")
    table.add_column("可见性")
    table.add_column("属主")
    for item in rows:
        table.add_row(
            str(item.id),
            str(item.name),
            str(item.members),
            str(item.visibility),
            "系统组" if item.owner_user_id is None else str(item.owner_user_id),
        )
    CONSOLE.print(table)


@group.command("create")
@click.argument("name")
@click.option("--public", "is_public", is_flag=True, help="组内账号对成员可见（默认私有）")
@click.option("--note", default="", help="备注")
def create_group(name: str, is_public: bool, note: str) -> None:
    """建组（命令行建的是**系统组**：属主留空，所有人可用）。"""

    async def action(container: Container) -> object:
        return await container.groups.create(
            name=name,
            owner_user_id=None,
            visibility="public" if is_public else "private",
            note=note,
        )

    try:
        created = run_with_container(action)
    except GroupExists as exc:
        CONSOLE.print(f"[red]已存在[/]{exc}")
        raise SystemExit(1) from exc

    CONSOLE.print(f"[green]已建组[/]{created.name}（ID {created.id}）")  # type: ignore[attr-defined]
    CONSOLE.print(f"加成员：cx group add {created.id} <账号ID> ...")  # type: ignore[attr-defined]


@group.command("add")
@click.argument("group_id", type=int)
@click.argument("account_ids", type=int, nargs=-1, required=True)
def add_members(group_id: int, account_ids: tuple[int, ...]) -> None:
    """把账号加入组。"""

    async def action(container: Container) -> int:
        return await container.groups.add_accounts(group_id, list(account_ids))

    try:
        added = run_with_container(action)
    except GroupNotFound as exc:
        CONSOLE.print(f"[red]不存在[/]{exc}")
        raise SystemExit(1) from exc

    CONSOLE.print(f"[green]已加入[/]{added} 个账号（组 {group_id}）")
    if added < len(account_ids):
        CONSOLE.print("[yellow]部分账号已在组里或不存在，已跳过[/]")


@group.command("remove")
@click.argument("group_id", type=int)
@click.argument("account_ids", type=int, nargs=-1, required=True)
def remove_members(group_id: int, account_ids: tuple[int, ...]) -> None:
    """把账号移出组（不删账号）。"""

    async def action(container: Container) -> int:
        return await container.groups.remove_accounts(group_id, list(account_ids))

    removed = run_with_container(action)
    CONSOLE.print(f"[green]已移出[/]{removed} 个账号（组 {group_id}）")


@group.command("delete")
@click.argument("group_id", type=int)
@click.option("--yes", is_flag=True, help="跳过确认")
def delete_group(group_id: int, yes: bool) -> None:
    """删组（组里的账号不受影响）。"""
    if not yes:
        click.confirm(f"确认删除组 {group_id}？（账号本身不会被删）", abort=True)

    async def action(container: Container) -> bool:
        return await container.groups.delete(group_id)

    if run_with_container(action):
        CONSOLE.print(f"[green]已删除[/]组 {group_id}")
    else:
        CONSOLE.print(f"[red]组 {group_id} 不存在[/]")
        raise SystemExit(1)
