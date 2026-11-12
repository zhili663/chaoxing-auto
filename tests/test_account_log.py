"""按账号隔离的日志：落盘、脱敏、读取。"""

from __future__ import annotations

import json
from pathlib import Path

from chaoxing.obs.account_log import AccountLogWriter


def test_writes_per_account_file_and_redacts(tmp_path: Path) -> None:
    writer = AccountLogWriter(tmp_path / "accounts")

    writer.write(
        7,
        "task_failed",
        point_id="p-1",
        payload={"Cookie": "session=secret", "nested": {"token": "abc"}, "msg": "作业提交失败！"},
    )

    path = tmp_path / "accounts" / "7.jsonl"
    assert path.exists()
    line = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert line["account_id"] == 7
    assert line["event"] == "task_failed"
    assert line["point_id"] == "p-1"
    assert line["payload"]["Cookie"] == "***"
    assert line["payload"]["nested"]["token"] == "***"
    assert line["payload"]["msg"] == "作业提交失败！"


def test_tail_is_bounded_and_keeps_order(tmp_path: Path) -> None:
    writer = AccountLogWriter(tmp_path / "accounts")
    for index in range(5):
        writer.write(7, "task_finished", index=index)

    lines = writer.tail(7, limit=2)

    assert len(lines) == 2
    assert json.loads(lines[-1])["index"] == 4


def test_different_accounts_go_to_different_files(tmp_path: Path) -> None:
    writer = AccountLogWriter(tmp_path / "accounts")
    writer.write(1, "task_started")
    writer.write(2, "task_started")

    assert (tmp_path / "accounts" / "1.jsonl").exists()
    assert (tmp_path / "accounts" / "2.jsonl").exists()
