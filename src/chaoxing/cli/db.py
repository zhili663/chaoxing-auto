"""``cx db``：数据库与迁移。

    cx db upgrade        把库升到最新迁移版本（内部调 Alembic 的 Python API）
    cx db stats          数据库文件、迁移版本、各表行数
    cx db shell          进入预置好引擎与模型的交互式 Python

迁移本身由 Alembic 管理（``uvx alembic revision --autogenerate``），
这里只提供"跑起来"与"看一眼"的入口。
"""

from __future__ import annotations

import code
import sqlite3
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from chaoxing.config import BASE_DIR, get_settings

CONSOLE = Console()


@click.group()
def db() -> None:
    """数据库与迁移。"""


@db.command("upgrade")
def upgrade() -> None:
    """把数据库升级到最新迁移版本（等价于 `alembic upgrade head`）。"""
    from alembic import command
    from alembic.config import Config

    config = Config(str(BASE_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BASE_DIR / "src/chaoxing/storage/migrations"))
    command.upgrade(config, "head")
    CONSOLE.print("[green]数据库已升级到最新版本[/]")


@db.command("revision")
@click.option("-m", "--message", required=True, help="迁移说明（会进文件名）")
@click.option("--autogenerate", is_flag=True, default=True, help="自动比对模型生成（默认开）")
def revision(message: str, autogenerate: bool) -> None:
    """生成一个新迁移（改完模型后执行）。"""
    from alembic import command
    from alembic.config import Config

    config = Config(str(BASE_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BASE_DIR / "src/chaoxing/storage/migrations"))
    command.revision(config, message=message, autogenerate=autogenerate)
    CONSOLE.print("[green]迁移已生成[/]记得 review 后再 `cx db upgrade`")


def _connect(path: Path) -> sqlite3.Connection:
    """只读连到库文件（stats 用）。"""
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


@db.command("stats")
def stats() -> None:
    """列出数据库文件、迁移版本与各表行数。"""
    settings = get_settings()
    path = settings.db_path
    if not path.exists():
        CONSOLE.print(f"[yellow]数据库尚未创建[/]{path} → 先跑 `cx db upgrade`")
        raise SystemExit(1)

    connection = _connect(path)
    try:
        version_row = connection.execute(
            "select version_num from alembic_version limit 1"
        ).fetchone()
        tables = [
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type='table' "
                "and name not like 'sqlite_%' order by name"
            )
        ]
        table = Table(title=f"{path.name}（{len(tables)} 张表）")
        table.add_column("表", style="bold")
        table.add_column("行数", justify="right")
        for name in tables:
            count = connection.execute(f'select count(*) from "{name}"').fetchone()
            table.add_row(name, str(count[0] if count else 0))
        CONSOLE.print(table)
        CONSOLE.print(
            f"迁移版本：{version_row[0] if version_row else '（无）'} · "
            f"文件：{path}（{path.stat().st_size / 1024:.0f} KB）"
        )
    finally:
        connection.close()


@db.command("import-legacy")
@click.option(
    "--legacy-path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="旧库路径（默认 data/chaoxing.db）",
)
@click.option("--dry-run", is_flag=True, help="只统计不写库")
def import_legacy(legacy_path: Path | None, dry_run: bool) -> None:
    """把旧库的账号与题库缓存导入新库（课程与任务点不迁，重扫更干净）。"""
    import asyncio

    from chaoxing.storage.engine import create_engine, create_session_factory
    from chaoxing.storage.legacy_import import ImportReport, import_legacy_data
    from chaoxing.storage.uow import UnitOfWork

    settings = get_settings()
    source = legacy_path or (settings.app.data_dir / "chaoxing.db")

    async def _run() -> ImportReport:
        engine = create_engine(settings.db_path)
        try:
            uow = UnitOfWork(create_session_factory(engine))
            async with uow.begin() as session:
                return await import_legacy_data(
                    source, session, secret_key=settings.secret_key, dry_run=dry_run
                )
        finally:
            await engine.dispose()

    report = asyncio.run(_run())

    table = Table(title=f"legacy 导入{'(dry-run)' if dry_run else ''}：{source.name}")
    table.add_column("项目")
    table.add_column("总数", justify="right")
    table.add_column("导入", justify="right")
    table.add_column("跳过", justify="right")
    table.add_row(
        "账号",
        str(report.accounts_total),
        str(report.accounts_imported),
        str(report.accounts_skipped + report.accounts_undecryptable),
    )
    table.add_row(
        "题库缓存",
        str(report.answers_total),
        str(report.answers_imported),
        str(report.answers_skipped),
    )
    CONSOLE.print(table)

    for warning in report.warnings:
        CONSOLE.print(f"[yellow]警告[/]{warning}")
    if not report.imported_anything and not report.warnings:
        CONSOLE.print("[yellow]旧库里没有可导入的数据[/]")
    CONSOLE.print("课程与任务点未迁移：用 `cx course scan` 重新扫描（学习通是唯一真相源）")


@db.command("shell")
def shell() -> None:
    """进入交互式 Python（预置 settings / engine / UoW / 模型）。"""
    from chaoxing.storage import models
    from chaoxing.storage.engine import create_engine, create_session_factory
    from chaoxing.storage.uow import UnitOfWork

    settings = get_settings()
    engine = create_engine(settings.db_path)
    factory = create_session_factory(engine)
    namespace = {
        "settings": settings,
        "engine": engine,
        "session_factory": factory,
        "uow": UnitOfWork(factory),
        "models": models,
    }
    CONSOLE.print("[dim]可用变量：settings / engine / session_factory / uow / models[/]")
    code.interact(banner="cx db shell —— Ctrl+Z 回车退出", local=namespace)
