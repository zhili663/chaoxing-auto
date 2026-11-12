"""``cx study``：刷课执行、控制与状态。

    cx study run    --account N [--course-id N]... [--limit N]
    cx study run    --all
    cx study status --account N
    cx study pause  --account N
    cx study resume --account N
    cx study reset  --account N [--course C]
    cx study logs   --account N [--course C] [--limit 50] [--raw]

这是"不启 API 也能刷课"的入口（计划 §9 P3 的 DoD）：CLI 与 API 共用同一个容器，
因此限速、事件、审计、数据可见性口径完全一致。

``pause`` / ``resume`` / ``reset`` / ``logs`` 只做两件事——**写一行或读一张表**：
状态机判定在 :mod:`chaoxing.core.services.study_control`，CLI 里不复制业务判定。
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from chaoxing.cli._runtime import run_with_container
from chaoxing.core.container import Container
from chaoxing.core.scheduler import SchedulerReport
from chaoxing.core.services.accounts import AccountNotFound
from chaoxing.core.services.study import StudyReport
from chaoxing.core.services.study_control import InvalidTransition, RunState
from chaoxing.storage.models import RUN_PAUSE_REQUESTED, RUN_RESUME_REQUESTED, TaskEvent

CONSOLE = Console()


@click.group()
def study() -> None:
    """刷课：执行与状态。"""


def _print_report(report: StudyReport) -> None:
    """打印单个账号的刷课报告。"""
    table = Table(title=f"账号 {report.account_id} 刷课报告")
    table.add_column("项目")
    table.add_column("值", justify="right")
    for label, value in (
        ("播放单元", report.units),
        ("完成", report.done),
        ("失败", report.failed),
        ("待人工", report.blocked),
        ("跳过", report.skipped),
        ("提前收尾", report.aborted),
    ):
        table.add_row(label, str(value))
    CONSOLE.print(table)
    for line in report.details[:20]:
        # 明细行以 ``[done]`` / ``[paused]`` 这类**状态码**开头，必须转义：
        # 不转义它们会被 rich 当成样式标签吃掉，报告里就看不到"为什么停了"。
        CONSOLE.print(f"  [dim]{escape(line)}[/]")
    if len(report.details) > 20:
        CONSOLE.print(f"  [dim]… 其余 {len(report.details) - 20} 条已省略[/]")


def _print_scheduler_report(result: SchedulerReport) -> None:
    """打印多账号并发调度的汇总：每个账号一张表 + 账号级失败清单。"""
    for report in result.reports.values():
        _print_report(report)
    for account_id, message in result.errors.items():
        CONSOLE.print(f"[red]账号 {account_id} 失败[/]{message}")


@study.command("run")
@click.option("--account", "account_id", type=int, help="账号 ID")
@click.option(
    "--accounts",
    "account_ids",
    type=str,
    default=None,
    help="并发指定账号（逗号分隔，如 1,2,3）",
)
@click.option("--all", "run_all", is_flag=True, help="并发刷所有启用中的账号")
@click.option("--course-id", "course_ids", type=int, multiple=True, help="限定课程（可多次）")
@click.option("--limit", type=int, default=None, help="最多执行多少个任务点（试跑用）")
def run(
    account_id: int | None,
    account_ids: str | None,
    run_all: bool,
    course_ids: tuple[int, ...],
    limit: int | None,
) -> None:
    """开始刷课。"""
    selected: list[int] = []
    if account_ids:
        try:
            selected = list(
                dict.fromkeys(int(piece) for piece in account_ids.split(",") if piece.strip())
            )
        except ValueError as exc:
            CONSOLE.print(f"[red]--accounts 格式不对（应如 1,2,3）：{exc}[/]")
            raise SystemExit(2) from exc
        if not selected:
            CONSOLE.print("[red]--accounts 里没有有效账号 ID[/]")
            raise SystemExit(2)

    picked = sum(1 for flag in (account_id, selected or None, run_all) if flag)
    if picked == 0:
        CONSOLE.print("[red]需要 --account N / --accounts 1,2,3 / --all 三者之一[/]")
        raise SystemExit(2)
    if picked > 1:
        CONSOLE.print("[red]--account、--accounts、--all 三者只能选一个[/]")
        raise SystemExit(2)

    courses = list(course_ids) or None

    if selected:

        async def action_selected(container: Container) -> SchedulerReport:
            known = {item.id for item in await container.accounts.list_accounts() if item.enabled}
            missing = [item for item in selected if item not in known]
            if missing:
                raise LookupError(f"账号不存在或未启用：{missing}（可选：{sorted(known)}）")
            return await container.scheduler.run(selected, course_ids=courses, limit=limit)

        try:
            result = run_with_container(action_selected)
        except LookupError as exc:
            CONSOLE.print(f"[red]{exc}[/]")
            raise SystemExit(2) from exc
        _print_scheduler_report(result)
        raise SystemExit(0 if result.ok else 1)

    if run_all:

        async def action_all(container: Container) -> SchedulerReport:
            accounts = [
                item.id for item in await container.accounts.list_accounts() if item.enabled
            ]
            if not accounts:
                return SchedulerReport()
            return await container.scheduler.run(accounts, course_ids=courses, limit=limit)

        result = run_with_container(action_all)
        if not result.reports and not result.errors:
            CONSOLE.print("[yellow]没有账号[/]先用 `cx account add` 或 `cx auth login`")
            return
        _print_scheduler_report(result)
        raise SystemExit(0 if result.ok else 1)

    assert account_id is not None  # 前置校验已保证

    async def action_one(container: Container) -> StudyReport:
        return await container.study.run_account(account_id, course_ids=courses, limit=limit)

    try:
        report = run_with_container(action_one)
    except Exception as exc:
        CONSOLE.print(f"[red]刷课失败[/]{type(exc).__name__}: {exc}")
        raise SystemExit(1) from exc

    _print_report(report)
    if report.aborted:
        CONSOLE.print(
            f"[yellow]已按要求提前收尾[/]（{report.abort_reason}）· "
            "已完成的进度已保留，下次 run 从断点继续"
        )
    if report.blocked:
        CONSOLE.print("[yellow]有任务点需要人工介入[/]（验证码 / 人脸抓拍）：请处理后重跑本命令")


@study.command("status")
@click.option("--account", "account_id", type=int, required=True, help="账号 ID")
def status(account_id: int) -> None:
    """查看任务点状态分布。"""

    async def action(container: Container) -> dict[str, int]:
        return await container.study.status(account_id)

    counts = run_with_container(action)
    if not counts:
        CONSOLE.print("[yellow]还没有任务点[/]")
        return

    table = Table(title=f"账号 {account_id} 的任务点")
    table.add_column("状态", style="bold")
    table.add_column("数量", justify="right")
    for state, count in sorted(counts.items()):
        table.add_row(state, str(count))
    CONSOLE.print(table)


# --------------------------------------------------------------------------- #
# 控制与记录（写一行 / 读一张表；判定在 study_control 服务里）
# --------------------------------------------------------------------------- #
async def _require_account(container: Container, account_id: int) -> None:
    """账号不存在时说人话（否则写控制行会撞外键约束，报错看不出原因）。"""
    try:
        await container.accounts.get(account_id)
    except AccountNotFound as exc:
        raise click.ClickException(f"账号 {account_id} 不存在") from exc


def _write_control(account_id: int, state: str) -> None:
    """写一行控制信号并打印结果状态。"""

    async def action(container: Container) -> RunState:
        await _require_account(container, account_id)
        try:
            return await container.study.control.request(account_id, state)
        except InvalidTransition as exc:
            raise click.ClickException(str(exc)) from exc

    view = run_with_container(action)
    CONSOLE.print(f"账号 {account_id}：[bold]{view.state}[/] · {view.note}")


@study.command("pause")
@click.option("--account", "account_id", type=int, required=True, help="账号 ID")
def pause(account_id: int) -> None:
    """请求暂停：运行器在检查点交出控制权，本次到此为止（进度保留）。"""
    _write_control(account_id, RUN_PAUSE_REQUESTED)


@study.command("resume")
@click.option("--account", "account_id", type=int, required=True, help="账号 ID")
def resume(account_id: int) -> None:
    """请求继续：随后重跑 `cx study run --account N` 即从断点接着刷。"""
    _write_control(account_id, RUN_RESUME_REQUESTED)


@study.command("reset")
@click.option("--account", "account_id", type=int, required=True, help="账号 ID")
@click.option("--course", "course_id", type=int, default=None, help="只重置某门课程（默认全部）")
def reset(account_id: int, course_id: int | None) -> None:
    """把任务点打回未开始（进度 / 错误 / 重试计数清零），**不删课程、账号与记录**。"""

    async def action(container: Container) -> tuple[int, RunState]:
        await _require_account(container, account_id)
        count = await container.study.control.reset_progress(account_id, course_id=course_id)
        return count, await container.study.control.current(account_id)

    count, view = run_with_container(action)
    scope = f"课程 {course_id}" if course_id else "全部课程"
    CONSOLE.print(
        f"账号 {account_id}（{scope}）：[bold]{count}[/] 个任务点已打回 queued · 控制状态 {view.state}"
    )


@study.command("logs")
@click.option("--account", "account_id", type=int, required=True, help="账号 ID")
@click.option("--course", "course_id", type=int, default=None, help="只看某门课程")
@click.option("--limit", type=int, default=50, help="最多显示多少条（上限 500；--raw 上限 2000）")
@click.option(
    "--raw",
    is_flag=True,
    help="读取按账号隔离的 data/logs/accounts/<id>.jsonl（含服务端原始返回与 traceback）",
)
def logs(account_id: int, course_id: int | None, limit: int, raw: bool) -> None:
    """倒序查看任务点事件流水（最新在前）；``--raw`` 看账号原始日志。"""

    if raw:

        async def raw_action(container: Container) -> list[str]:
            await _require_account(container, account_id)
            return container.account_logs.tail(account_id, limit=limit)

        lines = run_with_container(raw_action)
        if not lines:
            CONSOLE.print("[yellow]没有账号日志[/]这个账号还没跑过刷课，或日志尚未产生")
            return
        click.echo("\n".join(lines))
        return

    async def action(container: Container) -> list[TaskEvent]:
        await _require_account(container, account_id)
        return await container.study.control.recent_events(
            account_id, course_id=course_id, limit=limit
        )

    rows = run_with_container(action)
    if not rows:
        CONSOLE.print("[yellow]没有事件[/]这个账号还没跑过刷课")
        return

    table = Table(title=f"账号 {account_id} 最近 {len(rows)} 条事件（最新在前）")
    for column in ("时间", "事件", "类型", "说明"):
        table.add_column(column)
    for row in rows:
        table.add_row(
            row.created_at.astimezone().strftime("%m-%d %H:%M:%S"),
            row.event,
            row.kind,
            row.message[:80],
        )
    CONSOLE.print(table)
