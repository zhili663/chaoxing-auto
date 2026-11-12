"""``cx course``：课程列表、扫描、勾选。

    cx course list   --account N [--selected]
    cx course scan   --account N
    cx course select --account N KEY [KEY ...]

"刷什么"由这里的 ``select`` 与网页 CRUD 决定 —— 代码里没有任何默认课程。
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from chaoxing.cli._runtime import run_with_container
from chaoxing.core.container import Container
from chaoxing.core.services.courses import CourseNotFound, NotLoggedIn, PointScanReport, ScanReport
from chaoxing.storage.models import Course

CONSOLE = Console()


@click.group()
def course() -> None:
    """课程：列表 / 扫描 / 勾选。"""


@course.command("list")
@click.option("--account", "account_id", type=int, required=True, help="账号 ID")
@click.option("--selected", is_flag=True, help="只看已勾选要刷的")
def list_courses(account_id: int, selected: bool) -> None:
    """列出本地缓存的课程（不联网）。"""

    async def action(container: Container) -> list[Course]:
        return await container.courses.list_courses(account_id, selected_only=selected)

    courses = run_with_container(action)
    if not courses:
        CONSOLE.print("[yellow]没有课程[/]先跑 `cx course scan --account N`")
        return

    table = Table(title=f"账号 {account_id} 的课程（{len(courses)} 门）")
    table.add_column("ID", justify="right")
    table.add_column("课程键")
    table.add_column("名称", overflow="fold")
    table.add_column("已勾选")
    for item in courses:
        table.add_row(
            str(item.id),
            item.course_key,
            item.title or "(无标题)",
            "✓" if item.selected else "",
        )
    CONSOLE.print(table)
    CONSOLE.print(f"勾选：cx course select --account {account_id} <课程键> ...")


@course.command("scan")
@click.option("--account", "account_id", type=int, required=True, help="账号 ID")
def scan(account_id: int) -> None:
    """从学习通拉取课程列表并写库（不覆盖已勾选状态）。"""

    async def action(container: Container) -> ScanReport:
        return await container.courses.scan(account_id)

    try:
        report = run_with_container(action)
    except NotLoggedIn as exc:
        CONSOLE.print(f"[red]尚未登录[/]{exc}")
        raise SystemExit(1) from exc

    CONSOLE.print(
        f"[green]扫描完成[/]共 {report.total} 门（新增 {report.created} / 更新 {report.updated}）"
    )


@course.command("points")
@click.option("--account", "account_id", type=int, required=True, help="账号 ID")
@click.option("--course", "course_key", required=True, help="课程键（courseId:clazzId）")
def scan_points(account_id: int, course_key: str) -> None:
    """扫描一门课的章节与任务点并入库（刷课前的必要一步）。"""

    async def action(container: Container) -> PointScanReport:
        return await container.courses.scan_points(account_id, course_key)

    try:
        report = run_with_container(action)
    except (NotLoggedIn, CourseNotFound) as exc:
        CONSOLE.print(f"[red]无法扫描[/]{exc}")
        raise SystemExit(1) from exc

    table = Table(title=f"任务点扫描：{course_key}")
    table.add_column("项目")
    table.add_column("值", justify="right")
    table.add_row("章节", str(report.chapters))
    table.add_row("新增任务点", str(report.created))
    table.add_row("更新任务点", str(report.updated))
    for kind, count in sorted(report.kinds.items()):
        table.add_row(f"  · {kind}", str(count))
    CONSOLE.print(table)

    for warning in report.warnings:
        CONSOLE.print(f"[yellow]警告[/]{warning}")
    if report.total:
        CONSOLE.print(f"接着可跑 `cx study run --account {account_id}`")


@course.command("select")
@click.option("--account", "account_id", type=int, required=True, help="账号 ID")
@click.argument("course_keys", nargs=-1, required=True)
def select(account_id: int, course_keys: tuple[str, ...]) -> None:
    """勾选要刷的课程（替换式：传一门课就是"只刷这一门"）。"""

    async def action(container: Container) -> int:
        return await container.courses.select(account_id, list(course_keys))

    selected = run_with_container(action)
    CONSOLE.print(f"[green]已勾选[/]{selected} 门课程（账号 {account_id}）")
