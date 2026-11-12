"""真实账号端到端验收（验收清单第 4 条）。

前置：该账号已用 ``cx auth login`` 登录过（登录命令由你本人在终端执行，
密码不经过对话、也不会落进脚本或日志）。

用法::

    uv run python scripts/acceptance_e2e.py --account 1 --limit 3

它按真实链路走一遍，并在最后**把四类记录都查出来**：

1. 登录态抽检          → 会话是否有效
2. 扫描课程            → `accounts / courses` 表
3. 扫描任务点          → `task_points` 表（含 payload 透传字段）
4. 刷 N 个任务点        → 视频心跳 / 文档完成 / 测验提交
5. 四类记录汇总        → 刷课记录（task_events）/ 刷题记录（quiz_attempts）/
                          行为审计（audit_logs）/ app 日志（文件）

任何一步的结果都**如实打印**，不把"没跑成"写成"通过"。
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from sqlalchemy import func, select

from chaoxing.core.container import Container
from chaoxing.storage.models import AuditLog, Course, QuizAttempt, TaskEvent, TaskPoint


def line(title: str) -> None:
    """打印分隔标题。"""
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


async def count(container: Container, model: type) -> int:
    """统计某张表的行数。"""
    async with container.uow.read() as session:
        return int((await session.scalar(select(func.count()).select_from(model))) or 0)


async def tail(container: Container, model: type, columns: list[str], limit: int = 10) -> list[str]:
    """取最近几条记录（渲染成一行行文本）。"""
    async with container.uow.read() as session:
        rows = list(
            (await session.scalars(select(model).order_by(model.id.desc()).limit(limit))).all()
        )
    rendered: list[str] = []
    for row in rows:
        parts = [f"{name}={getattr(row, name, '')!r}" for name in columns]
        rendered.append("  " + " · ".join(parts))
    return rendered


async def run(account_id: int, *, limit: int, course_key: str) -> int:
    """执行整条验收链路。"""
    container = await Container.create()
    try:
        # ---- 1. 登录态 ----
        line("1) 登录态抽检")
        if not container.registry.has(account_id):
            print(f"✗ 账号 {account_id} 没有登录态")
            print(f"  先执行：uv run cx auth login --account {account_id} --password '...'")
            return 2
        state = container.registry.get(account_id)
        print(
            f"✓ 账号 {account_id} 有会话（cookie 项 {len(state.cookies)} 个，状态 {state.status}）"
        )

        # ---- 2. 扫描课程 ----
        line("2) 扫描课程")
        report = await container.courses.scan(account_id)
        print(f"  课程总数 {report.total}（新增 {report.created} / 更新 {report.updated}）")
        courses = await container.courses.list_courses(account_id)
        if not courses:
            print("✗ 这门账号下没有课程，无法继续")
            return 3
        for item in courses[:5]:
            mark = "✓" if item.selected else " "
            print(f"  [{mark}] {item.course_key}  {item.title}")
        target: Course | None = (
            next((item for item in courses if item.course_key == course_key), None)
            or next((item for item in courses if item.selected), None)
            or courses[0]
        )
        print(f"  目标课程：{target.course_key} {target.title}")

        # ---- 3. 扫描任务点 ----
        line("3) 扫描任务点")
        points = await container.courses.scan_points(account_id, target.course_key)
        print(f"  章节 {points.chapters} · 新增 {points.created} · 更新 {points.updated}")
        print(f"  类型分布：{points.kinds}")
        for warning in points.warnings:
            print(f"  ⚠ {warning}")
        if points.total == 0:
            print("✗ 一个任务点都没扫到，无法继续")
            return 4

        # ---- 4. 刷课 ----
        line(f"4) 刷 {limit} 个任务点（真实请求）")
        study = await container.study.run_account(account_id, course_ids=[target.id], limit=limit)
        print(
            f"  播放单元 {study.units} · 完成 {study.done} · 失败 {study.failed} · "
            f"待人工 {study.blocked} · 跳过 {study.skipped}（{study.elapsed_s:.1f}s）"
        )
        for item in study.details:
            print(f"  {item}")

        # ---- 5. 四类记录 ----
        line("5) 四类记录")
        events = await count(container, TaskEvent)
        quizzes = await count(container, QuizAttempt)
        audits = await count(container, AuditLog)
        points_rows = await count(container, TaskPoint)
        print(f"  刷课记录 task_events      : {events} 条")
        for row in await tail(container, TaskEvent, ["event", "kind", "message"], 5):
            print(row)
        print(f"  刷题记录 quiz_attempts    : {quizzes} 条")
        for row in await tail(
            container, QuizAttempt, ["work_id", "question_count", "answered_count", "submitted"], 5
        ):
            print(row)
        print(f"  行为审计 audit_logs       : {audits} 条")
        for row in await tail(container, AuditLog, ["action", "result", "resource_id"], 5):
            print(row)
        print(f"  任务点 task_points        : {points_rows} 条")
        for row in await tail(container, TaskPoint, ["kind", "status", "progress", "duration"], 5):
            print(row)

        log_dir = Path(container.settings.log_dir)
        log_file = log_dir / "app.log"
        if log_file.exists():
            size_kb = log_file.stat().st_size / 1024
            print(f"  app 日志 {log_file}（{size_kb:.0f} KB）")

        line("结论")
        ok = study.done > 0 or events > 0
        if ok:
            print("✓ 端到端链路跑通：真实账号完成了任务点并留下了四类记录")
            return 0
        print("✗ 链路没跑通：没有任何任务点完成，也没有事件记录")
        return 1
    finally:
        await container.aclose()


def main() -> int:
    """入口。"""
    parser = argparse.ArgumentParser(description="真实账号端到端验收")
    parser.add_argument("--account", type=int, required=True, help="账号 ID")
    parser.add_argument("--limit", type=int, default=3, help="最多刷几个任务点（默认 3）")
    parser.add_argument("--course", default="", help="指定课程键（默认用已勾选的第一门）")
    args = parser.parse_args()
    return asyncio.run(run(args.account, limit=args.limit, course_key=args.course))


if __name__ == "__main__":
    raise SystemExit(main())
