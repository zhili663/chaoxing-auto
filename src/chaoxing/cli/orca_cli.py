"""orca-cli 薄封装：抓包与网页测试都走它，本仓库不再自建浏览器驱动。

命令契约以 **2026-10-11 实测**为准（``docs/PROTOCOL_COVERAGE.md``）：

* ``orca tab create --json`` → ``result.browserPageId``；
* ``orca exec --command "network har start" --json`` → ``result.started``（**不接受路径**）；
* ``orca exec --command "network har stop" --json`` → ``result.path`` + ``result.requestCount``
  （路径由 stop 返回，写到 ``%USERPROFILE%\\.agent-browser\\tmp\\har\\`` 下）；
* ``orca tab close --json`` → ``result.closed``。

这一层的价值只有两个：把"找不到 orca / 它没在跑 / 命令失败"变成**一句能看懂的话**，
以及让上层能注入替身做离线测试。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from chaoxing.config import get_settings


class OrcaUnavailable(RuntimeError):
    """找不到 orca 可执行文件，或它没能正常执行。"""


def orca_bin() -> str:
    """orca 可执行文件路径（``APP__ORCA_BIN`` 优先，其次 PATH 上的 ``orca``）。

    按仓库的零硬编码规则，环境变量一律经 :func:`chaoxing.config.get_settings` 读取。

    :raises OrcaUnavailable: 两者都拿不到
    """
    configured = get_settings().app.orca_bin
    if configured:
        return configured
    found = shutil.which("orca")
    if not found:
        raise OrcaUnavailable("找不到 orca：先安装 Orca，或设置 APP__ORCA_BIN 指向它的可执行文件")
    return found


def run_orca(*args: str, timeout: float = 180.0) -> str:
    """跑一条 orca 命令并返回 stdout（参数**原样**传递，``--json`` 由调用方写）。

    :param args: 子命令与参数
    :param timeout: 单条命令的超时（秒）
    :raises OrcaUnavailable: 可执行文件缺失、超时或退出码非零
    :returns: 原始 stdout
    """
    command = [orca_bin(), *args]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OrcaUnavailable(f"orca 执行失败：{exc}") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[:300]
        raise OrcaUnavailable(f"orca {args[0]} 退出码 {completed.returncode}：{detail}")
    return completed.stdout


def orca_result(output: str) -> dict[str, Any]:
    """解包 orca 的 ``{"ok": bool, "result": {...}}``。

    :param output: :func:`run_orca` 的 stdout
    :raises OrcaUnavailable: 不是 JSON、或 ``ok`` 为假
    :returns: ``result`` 字典（缺失时返回空字典）
    """
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise OrcaUnavailable(f"orca 输出不是 JSON：{output.strip()[:200]}") from exc

    if not isinstance(payload, dict) or not payload.get("ok"):
        raise OrcaUnavailable(f"orca 返回失败：{output.strip()[:300]}")

    result = payload.get("result")
    return result if isinstance(result, dict) else {}


__all__ = ["OrcaUnavailable", "orca_bin", "orca_result", "run_orca"]
