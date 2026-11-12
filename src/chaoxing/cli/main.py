"""``cx`` 命令组（click）。

设计约束见 ``docs/REFACTOR_PLAN.md`` §5.9：

* 只收**后端本该有的运维与开发者命令**，允许两级嵌套（``cx auth login``）；
* **禁止**把 ``tools/`` 里的脚本 1:1 搬成子命令 ——
  ``test-*`` / ``smoke-*`` / ``check-*`` / ``diag-*`` / ``e2e-*`` / ``preview-*`` 一律不存在；
* 同一容器保证 CLI 与 API 行为一致（同一份限速、事件、审计、数据可见性过滤）。
"""

from __future__ import annotations

import contextlib
import sys

import click

from chaoxing import __version__
from chaoxing.cli.account import account
from chaoxing.cli.auth import auth
from chaoxing.cli.course import course
from chaoxing.cli.db import db
from chaoxing.cli.dev import dev
from chaoxing.cli.doctor import doctor
from chaoxing.cli.group import group
from chaoxing.cli.proxy import proxy
from chaoxing.cli.serve import serve
from chaoxing.cli.settings import settings
from chaoxing.cli.stats import stats
from chaoxing.cli.study import study
from chaoxing.cli.user import user

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"], "max_content_width": 100}


def force_utf8_stdio() -> None:
    """把标准流切到 UTF-8。

    Windows 默认用 GBK 编码 stdout，rich 的符号与中文会直接
    ``UnicodeEncodeError``（实测崩在 ``console.print``）；这里统一成 UTF-8，
    现代终端（Windows Terminal / PowerShell 7 / CI）都能正确显示。
    不支持的流（例如测试里的 CliRunner 替身）会被静默跳过。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with contextlib.suppress(OSError, ValueError):
            reconfigure(encoding="utf-8", errors="replace")


@click.group(name="cx", context_settings=CONTEXT_SETTINGS)
@click.version_option(__version__, "-V", "--version", prog_name="cx")
def cli() -> None:
    """学习通刷课服务命令行。

    命令树：``cx doctor`` / ``cx auth`` / ``cx account`` / ``cx group`` /
    ``cx course`` / ``cx study`` / ``cx user`` / ``cx db`` / ``cx dev`` /
    ``cx settings`` / ``cx stats`` / ``cx serve``。
    每个域的 ``--help`` 是权威说明（``tests/test_cli_surface.py`` 守着命令树边界）。
    """
    force_utf8_stdio()


cli.add_command(doctor)
cli.add_command(auth)
cli.add_command(account)
cli.add_command(group)
cli.add_command(proxy)
cli.add_command(dev)
cli.add_command(db)
cli.add_command(course)
cli.add_command(study)
cli.add_command(stats)
cli.add_command(serve)
cli.add_command(settings)
cli.add_command(user)


def main() -> None:
    """控制台入口（``cx`` 命令实际调用的是它）。"""
    force_utf8_stdio()
    cli()


if __name__ == "__main__":  # pragma: no cover - 手工执行入口
    main()
