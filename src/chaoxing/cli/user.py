"""``cx user``：平台用户与角色（**与超星账号无关**）。

    cx user list                        列出用户（含角色）
    cx user create -u 用户名 [--role R]  建号（口令交互式输入，不回显）
    cx user grant  ID ROLE / revoke ID ROLE
    cx user passwd ID                   改密（同时撤销该用户全部会话）

口令口径与网页一致：**先在前端/命令行做 sha256，再把哈希交给后端存 Argon2id** ——
明文既不进数据库，也不进 shell 历史（``-p`` 留空就是交互式输入）。
"""

from __future__ import annotations

import hashlib

import click
from rich.console import Console
from rich.table import Table

from chaoxing.cli._runtime import run_with_container
from chaoxing.core.container import Container
from chaoxing.core.services.users import BUILTIN_ROLES, UserExists

CONSOLE = Console()


def client_secret(password: str) -> str:
    """把明文口令转成后端要的形态（与网页 ``crypto.subtle` 同一口径）。"""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def prompt_secret() -> str:
    """交互式要两次口令（不回显、需一致）。"""
    while True:
        first = click.prompt("口令", hide_input=True, confirmation_prompt=True)
        if first:
            return client_secret(first)
        CONSOLE.print("[yellow]口令不能为空[/]")


@click.group()
def user() -> None:
    """平台用户与角色：列表 / 建号 / 授权 / 改密。"""


@user.command("list")
def list_users() -> None:
    """列出用户与角色。"""

    async def action(container: Container) -> list[tuple[object, list[str]]]:
        from sqlalchemy import select

        from chaoxing.storage.models import Role, user_roles

        rows: list[tuple[object, list[str]]] = []
        for item in await container.users.list_users():
            async with container.uow.read() as db:
                roles = list(
                    (
                        await db.scalars(
                            select(Role.name)
                            .select_from(user_roles)
                            .join(Role, Role.id == user_roles.c.role_id)
                            .where(user_roles.c.user_id == item.id)
                        )
                    ).all()
                )
            rows.append((item, [str(name) for name in roles]))
        return rows

    rows = run_with_container(action)
    if not rows:
        CONSOLE.print("[yellow]还没有用户[/]用 `cx user create -u <用户名>` 建号")
        return

    table = Table(title=f"平台用户（{len(rows)}）")
    table.add_column("ID", justify="right")
    table.add_column("用户名")
    table.add_column("角色")
    table.add_column("超管")
    for item, roles in rows:
        table.add_row(
            str(item.id),  # type: ignore[attr-defined]
            item.username,  # type: ignore[attr-defined]
            ", ".join(roles) or "（无）",
            "✓" if item.is_superuser else "",  # type: ignore[attr-defined]
        )
    CONSOLE.print(table)


@user.command("create")
@click.option("-u", "--username", required=True, help="登录名")
@click.option("--role", default="viewer", show_default=True, help="初始角色")
@click.option("--display-name", default="", help="展示名")
def create_user(username: str, role: str, display_name: str) -> None:
    """建号（首个用户自动成为超管）。"""
    secret = prompt_secret()

    async def action(container: Container) -> object:
        created = await container.users.create(
            username=username, client_secret=secret, display_name=display_name
        )
        if role:
            await container.users.grant(created.id, role)
        return created

    try:
        created = run_with_container(action)
    except UserExists as exc:
        CONSOLE.print(f"[red]已存在[/]{exc}")
        raise SystemExit(1) from exc

    flag = " [yellow]（首个用户，自动超管）[/]" if created.is_superuser else ""  # type: ignore[attr-defined]
    CONSOLE.print(f"[green]已创建[/]{created.username}（ID {created.id}）{flag}")  # type: ignore[attr-defined]


@user.command("grant")
@click.argument("user_id", type=int)
@click.argument("role")
def grant_role(user_id: int, role: str) -> None:
    """授予角色。"""

    async def action(container: Container) -> bool:
        return await container.users.grant(user_id, role)

    if run_with_container(action):
        CONSOLE.print(f"[green]已授予[/]用户 {user_id} 角色 {role}")
    else:
        CONSOLE.print(f"[red]用户 {user_id} 不存在[/]")
        raise SystemExit(1)


@user.command("revoke")
@click.argument("user_id", type=int)
@click.argument("role")
def revoke_role(user_id: int, role: str) -> None:
    """收回角色。"""

    async def action(container: Container) -> bool:
        return await container.users.revoke(user_id, role)

    if run_with_container(action):
        CONSOLE.print(f"[green]已收回[/]用户 {user_id} 的角色 {role}")
    else:
        CONSOLE.print(f"[yellow]用户 {user_id} 没有角色 {role}（或用户不存在）[/]")


@user.command("passwd")
@click.argument("user_id", type=int)
def change_password(user_id: int) -> None:
    """改密并**撤销该用户全部会话**（旧会话不该继续有效）。"""
    secret = prompt_secret()

    async def action(container: Container) -> tuple[bool, int]:
        changed = await container.users.set_password(user_id, secret)
        revoked = await container.users.close_all_sessions(user_id) if changed else 0
        return changed, revoked

    changed, revoked = run_with_container(action)
    if changed:
        CONSOLE.print(f"[green]已改密[/]用户 {user_id}，同时撤销了 {revoked} 个会话")
    else:
        CONSOLE.print(f"[red]用户 {user_id} 不存在[/]")
        raise SystemExit(1)


@user.command("roles")
def list_roles() -> None:
    """列出内置角色与它们的权限节点。"""
    table = Table(title="内置角色")
    table.add_column("角色")
    table.add_column("权限节点")
    for name, nodes in BUILTIN_ROLES.items():
        table.add_row(name, "\n".join(nodes))
    CONSOLE.print(table)
