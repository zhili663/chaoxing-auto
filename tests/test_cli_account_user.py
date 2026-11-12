"""``cx account`` 与 ``cx user`` 两个 CLI 域。

这两个域补的是"网页能用、命令行也能用"的问题 —— 尤其 `cx account`：
课程与任务点挂在账号实体下（外键约束），而 `cx auth login` **只写登录态、不建实体**，
没有建号入口时从零开始根本跑不通。

测试用临时数据目录，不碰真实库（共享夹具 ``env`` 见 ``tests/conftest.py``）。
"""

from __future__ import annotations

from click.testing import CliRunner

from chaoxing.cli.main import cli


def test_account_add_and_list(env: None) -> None:
    """新增账号后能在列表里看到（并提示下一步去登录）。"""
    runner = CliRunner()

    added = runner.invoke(
        cli, ["account", "add", "-u", "13800000000", "-p", "pw", "--note", "机房A"]
    )
    assert added.exit_code == 0, added.output
    assert "已新增" in added.output
    assert "cx auth login" in added.output  # 下一步命令要能照抄

    listed = runner.invoke(cli, ["account", "list"])
    assert listed.exit_code == 0, listed.output
    assert "13800000000" in listed.output
    assert "机房A" in listed.output


def test_account_duplicate_is_rejected(env: None) -> None:
    """同名账号不能再加（否则外键与去重都会乱）。"""
    runner = CliRunner()
    runner.invoke(cli, ["account", "add", "-u", "13800000000", "-p", "pw"])

    again = runner.invoke(cli, ["account", "add", "-u", "13800000000", "-p", "pw"])

    assert again.exit_code == 1
    assert "已存在" in again.output


def test_account_disable_and_remove(env: None) -> None:
    """停用与删除都要真的生效。"""
    runner = CliRunner()
    runner.invoke(cli, ["account", "add", "-u", "13800000001", "-p", "pw"])
    # 临时库是干净的，自增从 1 开始
    account_id = 1

    assert runner.invoke(cli, ["account", "disable", str(account_id)]).exit_code == 0
    assert "已停用" in runner.invoke(cli, ["account", "disable", str(account_id)]).output

    removed = runner.invoke(cli, ["account", "remove", str(account_id), "--yes"])
    assert removed.exit_code == 0
    assert "已删除" in removed.output

    assert "还没有账号" in runner.invoke(cli, ["account", "list"]).output


def test_account_operations_on_missing_id_fail_cleanly(env: None) -> None:
    """对不存在的账号操作要明确报错（退出码非零），而不是静默成功。"""
    result = CliRunner().invoke(cli, ["account", "disable", "999"])

    assert result.exit_code == 1
    assert "不存在" in result.output


def test_user_create_grant_and_passwd(env: None) -> None:
    """建号（含交互式口令）→ 授权 → 改密撤销会话。"""
    runner = CliRunner()

    created = runner.invoke(
        cli,
        ["user", "create", "-u", "alice", "--role", "operator"],
        input="secret-pass\nsecret-pass\n",  # 口令交互式输入两次，不回显
    )
    assert created.exit_code == 0, created.output
    assert "已创建" in created.output
    assert "自动超管" in created.output  # 库里第一个用户

    listed = runner.invoke(cli, ["user", "list"])
    assert "alice" in listed.output
    assert "operator" in listed.output

    assert runner.invoke(cli, ["user", "revoke", "1", "operator"]).exit_code == 0
    granted = runner.invoke(cli, ["user", "grant", "1", "admin"])
    assert granted.exit_code == 0
    assert "已授予" in granted.output

    changed = runner.invoke(cli, ["user", "passwd", "1"], input="new-pass\nnew-pass\n")
    assert changed.exit_code == 0
    assert "已改密" in changed.output


def test_user_create_rejects_duplicate(env: None) -> None:
    """同名用户不能再建。"""
    runner = CliRunner()
    runner.invoke(cli, ["user", "create", "-u", "bob"], input="pw\npw\n")

    again = runner.invoke(cli, ["user", "create", "-u", "bob"], input="pw\npw\n")

    assert again.exit_code == 1
    assert "已存在" in again.output


def test_user_roles_lists_permission_nodes(env: None) -> None:
    """角色清单要能直接看出每个角色有哪些权限节点。"""
    result = CliRunner().invoke(cli, ["user", "roles"])

    assert result.exit_code == 0
    assert "viewer" in result.output
    assert "account:read" in result.output
