"""Alembic 迁移环境。

三处定制（其余保持 ``uvx alembic init -t async`` 模板原样）：

1. ``target_metadata`` 指向项目的 ``Base.metadata``，并**显式导入全部模型模块** ——
   autogenerate 只认识已注册的表，漏一个模块会静默少一张表；
2. 连接串从 :func:`chaoxing.config.get_settings` 取，避免 ``alembic.ini`` 与 ``.env``
   两处配置打架；
3. ``render_as_batch=True`` —— SQLite 不支持 ``ALTER COLUMN``，改表必须走 batch 重建。
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from chaoxing.config import get_settings
from chaoxing.storage import models  # noqa: F401 - 导入即注册，autogenerate 依赖它
from chaoxing.storage.base import Base
from chaoxing.storage.engine import database_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

#: 建库用的元数据（模型全部注册在上面那行 import 里完成）
target_metadata = Base.metadata


def _database_url() -> str:
    """从项目配置解析数据库 URL（``.env`` 是唯一来源）。"""
    return database_url(get_settings().db_path)


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连库。"""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """在线模式的实际执行体（由 async 引擎经 ``run_sync`` 调用）。"""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """用 async 引擎跑迁移。"""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """在线模式入口。"""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
