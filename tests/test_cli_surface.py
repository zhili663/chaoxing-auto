"""CLI 命令树的约束检查（docs/REFACTOR_PLAN.md §5.9）。

对应 rev4 的明确要求：**禁止把 tools/ 脚本 1:1 搬成子命令**，
``smoke-test`` 这类"根本不该出现在后端 CLI 里"的命令必须不存在。
"""

from __future__ import annotations

import subprocess
import sys

import click
from click.testing import CliRunner

from chaoxing.cli.main import cli

#: 允许出现的顶层命令（新增命令时必须同步这份清单，改动即评审点）
ALLOWED_TOP_LEVEL = {
    "serve",
    "doctor",
    "auth",
    "account",
    "group",
    "course",
    "study",
    "stats",
    "settings",
    "user",
    "db",
    "dev",
    "proxy",
}

#: 任何层级都不允许出现的前缀：测试 / 校检 / 诊断 / 端到端 / 页面预览
FORBIDDEN_PREFIXES = ("test", "smoke", "check", "diag", "diagnose", "e2e", "preview", "live")


def _walk(group: click.Group, prefix: str = "") -> list[str]:
    """递归展开命令树，返回形如 ``auth login`` 的全名列表。"""
    names: list[str] = []
    for name, command in group.commands.items():
        full = f"{prefix}{name}"
        names.append(full)
        if isinstance(command, click.Group):
            names.extend(_walk(command, prefix=f"{full} "))
    return names


def test_top_level_commands_are_bounded() -> None:
    """顶层命令只能是规范里定义的那几个域。"""
    unexpected = set(cli.commands) - ALLOWED_TOP_LEVEL

    assert not unexpected, f"未在规范内的顶层命令：{sorted(unexpected)}"


def test_no_forbidden_subcommands_anywhere() -> None:
    """任何层级都不允许 test-* / smoke-* / check-* / diag-* / e2e-* / preview-*。"""
    offenders = [name for name in _walk(cli) if name.split()[-1].startswith(FORBIDDEN_PREFIXES)]

    assert not offenders, f"CLI 不得出现这些子命令：{offenders}"


def test_doctor_exposes_documented_flags() -> None:
    """doctor 的四个模式必须真实存在（§5.9 的契约）。"""
    result = CliRunner().invoke(cli, ["doctor", "--help"])

    assert result.exit_code == 0
    for flag in ("--full", "--raw", "--fix"):
        assert flag in result.output, f"doctor 缺少 {flag}"


def test_root_help_lists_doctor() -> None:
    """根帮助里能看到 doctor（CLI 入口确实挂上了）。"""
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "doctor" in result.output


def test_python_dash_m_entrypoint_works() -> None:
    """计划 §3 要求 `python -m chaoxing` 等价于 `cx`。"""
    result = subprocess.run(
        [sys.executable, "-m", "chaoxing", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "doctor" in result.stdout
