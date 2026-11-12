"""``cx settings``：运行时配置覆盖（第三层）。

    cx settings show                      列出可调项（敏感项脱敏）
    cx settings set KEY VALUE             写入覆盖
    cx settings unset KEY                 删除覆盖（回到 .env / 默认）

只有白名单里的键可改（见 ``core.services.preferences``）——
监听地址、数据库路径、SECRET_KEY 这类"改了会把自己锁在门外"的项不在其中。
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from chaoxing.cli._runtime import run_with_container
from chaoxing.core.container import Container
from chaoxing.core.services.preferences import InvalidValue, UnknownSetting

CONSOLE = Console()


@click.group()
def settings() -> None:
    """运行时配置覆盖。"""


@settings.command("show")
def show() -> None:
    """列出可调项（当前值 / 默认值 / 是否被覆盖）。"""

    async def action(container: Container) -> dict[str, dict[str, object]]:
        return await container.preferences.describe()

    described = run_with_container(action)

    table = Table(title="配置项（数据库覆盖层）")
    table.add_column("键")
    table.add_column("当前值")
    table.add_column("默认值")
    table.add_column("已覆盖")
    for key, item in described.items():
        table.add_row(
            key,
            str(item.get("value")),
            str(item.get("default")),
            "✓" if item.get("overridden") else "",
        )
    CONSOLE.print(table)
    CONSOLE.print("改一项：cx settings set chaoxing.video_rate 1.5")


@settings.command("set")
@click.argument("key")
@click.argument("value")
def set_value(key: str, value: str) -> None:
    """写入一条覆盖（KEY 形如 chaoxing.video_rate）。"""

    async def action(container: Container) -> object:
        return await container.preferences.set(key, value)

    try:
        applied = run_with_container(action)
    except UnknownSetting as exc:
        CONSOLE.print(f"[red]不可修改[/]{exc}")
        raise SystemExit(1) from exc
    except InvalidValue as exc:
        CONSOLE.print(f"[red]取值不合法[/]{exc}")
        raise SystemExit(1) from exc

    CONSOLE.print(f"[green]已生效[/]{key} = {applied!r}（重启后依然有效）")


@settings.command("unset")
@click.argument("key")
def unset_value(key: str) -> None:
    """删除一条覆盖（回到 .env / 代码默认）。"""

    async def action(container: Container) -> bool:
        return await container.preferences.unset(key)

    removed = run_with_container(action)
    if removed:
        CONSOLE.print(f"[green]已删除覆盖[/]{key}")
    else:
        CONSOLE.print(f"[yellow]没有这条覆盖[/]{key}")
