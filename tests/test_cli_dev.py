"""``cx dev`` 的探针与抓包命令：无会话行为、输出契约、orca 驱动顺序。

来源（都是真实现象，不是编的）：

* 2026-10 真机排查 —— 心跳 403 的根因是「dtoken 需要一次真实播放才激活」，
  见 ``docs/MOOC_ANS_403.md`` §五；
* 2026-10-11 抓包改用 orca-cli 后实测 —— ``network har start`` 必须在**导航之前**，
  顺序反了 ``requestCount`` 恒为 0，HAR 里一条请求都没有。

无会话用例共享 ``tests/conftest.py`` 里的 ``env`` 夹具（临时数据目录）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from chaoxing.cli import dev as dev_module
from chaoxing.cli.dev import describe_probe
from chaoxing.cli.main import cli
from chaoxing.client.model.video import ActivationProbe


def test_probe_heartbeat_requires_session(env: None) -> None:
    """没有登录态时给出可照抄的下一步，而不是抛栈。"""
    result = CliRunner().invoke(
        cli, ["dev", "probe", "heartbeat", "--account", "1", "--object-id", "abc"]
    )

    assert result.exit_code == 1
    assert "cx auth login" in result.output


def test_describe_probe_prints_both_dtokens_and_caveat() -> None:
    """两个 dtoken 都要露出，并带上适用性说明 —— 否则读数会被误读。"""
    lines = describe_probe(
        ActivationProbe(object_id="abc", dtoken_before="aaa111", dtoken_after="bbb222"), ()
    )
    text = "\n".join(lines)

    assert "aaa111" in text
    assert "bbb222" in text
    assert "播放器页" in text
    assert "适用" in text


def test_capture_browser_drives_orca_and_writes_tool_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """抓包必须由 orca-cli 驱动：建标签页 → 开 HAR → 导航 → 停 HAR → 落成工具格式。

    真实契约（2026-10-11 实测）：HAR 路径由 `network har stop` 的返回值给出，
    `start` 不接受路径；顺序反了（先导航再开 HAR）就一条都录不到 —— 这条顺序断言
    就是那次实测的固化。
    """
    calls: list[tuple[str, ...]] = []
    har = tmp_path / "har-1.har"

    def fake_run(*args: str) -> str:
        calls.append(args)
        joined = " ".join(args)
        if "network har stop" in joined:
            har.write_text(
                json.dumps(
                    {
                        "log": {
                            "entries": [
                                {
                                    "request": {
                                        "url": "https://mooc1.chaoxing.com/visit/courselistdata"
                                    }
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            return json.dumps({"ok": True, "result": {"path": str(har), "requestCount": 1}})
        if "network har start" in joined:
            return json.dumps({"ok": True, "result": {"started": True}})
        if args[:2] == ("tab", "create"):
            return json.dumps({"ok": True, "result": {"browserPageId": "page-1"}})
        return json.dumps({"ok": True, "result": {}})

    monkeypatch.setattr(dev_module, "run_orca", fake_run)
    out = tmp_path / "session.json"

    result = CliRunner().invoke(
        cli,
        [
            "dev",
            "capture",
            "browser",
            "--url",
            "https://mooc1.chaoxing.com/study",
            "--out",
            str(out),
            "--seconds",
            "0",
        ],
    )

    assert result.exit_code == 0, result.output
    joined = [" ".join(call) for call in calls]

    def index_of(needle: str) -> int:
        return next(index for index, call in enumerate(joined) if needle in call)

    assert calls[0][:2] == ("tab", "create")
    assert index_of("network har start") < index_of("goto"), "HAR 必须在导航之前就开着"
    assert index_of("network har stop") > index_of("goto")
    assert json.loads(out.read_text(encoding="utf-8"))["requests"][0]["url"].endswith(
        "/visit/courselistdata"
    )
