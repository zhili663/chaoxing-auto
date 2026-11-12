"""``cx stats``：全局 KPI 与按天活动。

    cx stats [--account N] [--days N]

"不启 API 也能看统计"—— CLI 与 API 共用同一份统计服务，口径不会出现两套。
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from chaoxing.cli._runtime import run_with_container
from chaoxing.core.container import Container
from chaoxing.core.services.stats import DailyStat

CONSOLE = Console()

#: 热力方块（按当天完成量取档）
_BLOCKS = ("·", "▁", "▂", "▃", "▄", "▅", "▆", "▇", "█")


def render_bar(stat: DailyStat, *, scale: int) -> str:
    """把一个按天数据渲染成一行方块（便于一眼看出哪几天在刷）。

    :param stat: 当天数据
    :param scale: 满格对应的完成数
    :returns: 方块字符串
    """
    if scale <= 0 or stat.points_done <= 0:
        return _BLOCKS[0]
    level = min(len(_BLOCKS) - 1, max(1, round(stat.points_done / scale * (len(_BLOCKS) - 1))))
    return _BLOCKS[level]


@click.command("stats")
@click.option("--account", "account_id", type=int, default=None, help="只看某账号（默认全部）")
@click.option("--days", type=int, default=14, show_default=True, help="展示最近多少天")
def stats(account_id: int | None, days: int) -> None:
    """查看累计 KPI 与最近的活动热力。"""

    async def action(container: Container) -> tuple[dict[str, float | int], list[DailyStat]]:
        kpi = await container.stats.kpi(account_id=account_id)
        daily = await container.stats.daily(account_id=account_id, days=max(1, days))
        return kpi, daily

    kpi, daily = run_with_container(action)

    scope = f"账号 {account_id}" if account_id else "全部账号"
    table = Table(title=f"KPI（{scope}）")
    table.add_column("项目")
    table.add_column("值", justify="right")
    for label, key in (
        ("课程总数", "courses"),
        ("已勾选课程", "selected_courses"),
        ("任务点总数", "points_total"),
        ("已完成", "points_done"),
        ("失败", "points_failed"),
        ("待人工", "points_need_human"),
        ("待刷", "points_queued"),
        ("测验完成", "quizzes_done"),
        ("累计学习（分钟）", "watched_minutes"),
    ):
        table.add_row(label, str(kpi.get(key, 0)))
    CONSOLE.print(table)

    if not daily:
        return

    scale = max((item.points_done for item in daily), default=0)
    heat = Table(title=f"最近 {len(daily)} 天")
    heat.add_column("日期")
    heat.add_column("热力")
    heat.add_column("完成", justify="right")
    heat.add_column("测验", justify="right")
    heat.add_column("分钟", justify="right")
    for item in daily:
        heat.add_row(
            item.day,
            render_bar(item, scale=scale),
            str(item.points_done),
            str(item.quiz_done),
            str(item.watched_minutes),
        )
    CONSOLE.print(heat)
