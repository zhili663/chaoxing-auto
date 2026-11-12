"""刷课控制面：HTTP 端点、权限、404 口径，以及 CLI 的 reset / logs。

来源：计划 Task P3。要钉住的口径：

* 暂停/继续要 ``study:plan:control``，读状态只要 ``study:record:read``；
* 别人的账号一律 **404**（与不存在同样处理，403 等于确认账号存在）；
* 非法转移是 **409** ``invalid_transition``；
* ``reset`` 只把任务点打回未开始，**不删**课程 / 账号 / 记录；``logs`` 倒序且有条数上限。

运行器侧（走到检查点）用**独立引擎**驱动 —— app 的引擎挂在 TestClient 自己的事件循环上，
不该被测试的 ``asyncio.run`` 抢过去用。
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from sqlalchemy import select

from chaoxing.cli.main import cli
from chaoxing.config import Settings
from chaoxing.core.container import Container
from chaoxing.core.services.study_control import StudyControlService
from chaoxing.storage.base import utcnow
from chaoxing.storage.engine import create_engine, create_session_factory
from chaoxing.storage.models import (
    RUN_PAUSE_REQUESTED,
    RUN_PAUSED,
    RUN_RESUME_REQUESTED,
    RUN_RUNNING,
    Account,
    Course,
    TaskEvent,
    TaskPoint,
    User,
)
from chaoxing.storage.uow import UnitOfWork


def secret_of(password: str) -> str:
    """前端口径的口令：``sha256(password)``。"""
    return hashlib.sha256(password.encode()).hexdigest()


def register(client: TestClient, username: str, password: str = "passw0rd") -> None:
    """走完整注册流程（非首个用户需要 nonce）。"""
    nonce = client.get("/api/auth/challenge", params={"scene": "register"}).json()["nonce"]
    response = client.post(
        "/api/auth/register",
        json={"username": username, "secret": secret_of(password), "nonce": nonce},
    )
    assert response.status_code == 201, response.text


def grant(client: TestClient, username: str, role: str) -> None:
    """直接经服务层授权（新注册用户默认没有任何角色）。"""
    container: Container = client.app.state.container  # type: ignore[attr-defined]

    async def run() -> None:
        async with container.uow.read() as db:
            row = (await db.scalars(select(User).where(User.username == username))).first()
        assert row is not None
        await container.users.grant(row.id, role)

    asyncio.run(run())


def from_db(settings: Settings, action: Callable[[UnitOfWork], Any]) -> Any:
    """在**独立引擎**上跑一段读写（不借用 app 的事件循环）。"""

    async def run() -> Any:
        engine = create_engine(settings.db_path)
        try:
            return await action(UnitOfWork(create_session_factory(engine)))
        finally:
            await engine.dispose()

    return asyncio.run(run())


def seed(settings: Settings, *, owner_user_id: int | None = None) -> None:
    """造数据：账号 1 + 两门课 + 三个任务点 + 三条事件。"""

    async def prepare(uow: UnitOfWork) -> None:
        async with uow.begin() as db:
            account = Account(username="13800000000", owner_user_id=owner_user_id)
            db.add(account)
            await db.flush()
            course = Course(account_id=account.id, course_key="1:2", title="课程 A")
            other = Course(account_id=account.id, course_key="3:4", title="课程 B")
            db.add_all([course, other])
            await db.flush()
            db.add_all(
                [
                    TaskPoint(
                        account_id=account.id,
                        course_id=course.id,
                        point_id="p1",
                        kind="video",
                        status="done",
                        progress=600.0,
                        duration=600.0,
                        sequence=1001,
                    ),
                    TaskPoint(
                        account_id=account.id,
                        course_id=course.id,
                        point_id="p2",
                        kind="quiz",
                        status="failed",
                        last_error="403",
                        retry_count=3,
                        sequence=1002,
                    ),
                    TaskPoint(
                        account_id=account.id,
                        course_id=other.id,
                        point_id="p3",
                        kind="video",
                        status="done",
                        progress=30.0,
                        duration=30.0,
                        sequence=2001,
                    ),
                ]
            )
            base = utcnow()
            for index in range(3):
                db.add(
                    TaskEvent(
                        account_id=account.id,
                        course_id=course.id,
                        kind="video",
                        event="finished",
                        message=f"e{index}",
                        created_at=base + timedelta(seconds=index),
                    )
                )

    from_db(settings, prepare)


def checkpoint(settings: Settings, account_id: int) -> str:
    """在运行器侧走一次检查点，返回 ``"动作:状态"``。"""

    async def run(uow: UnitOfWork) -> str:
        verdict = await StudyControlService(uow=uow).checkpoint(account_id)
        return f"{verdict.action}:{verdict.state}"

    return str(from_db(settings, run))


def snapshot(settings: Settings) -> dict[str, Any]:
    """库里现在的样子（断言用）。"""

    async def run(uow: UnitOfWork) -> dict[str, Any]:
        async with uow.read() as db:
            points = list((await db.scalars(select(TaskPoint).order_by(TaskPoint.id))).all())
            return {
                "points": [(item.point_id, item.status, item.progress) for item in points],
                "last_errors": [item.last_error for item in points],
                "retries": [item.retry_count for item in points],
                "courses": len((await db.scalars(select(Course))).all()),
                "accounts": len((await db.scalars(select(Account))).all()),
                "events": len((await db.scalars(select(TaskEvent))).all()),
            }

    return dict(from_db(settings, run))


@pytest.fixture
def seeded(logged_in: TestClient, settings: Settings) -> TestClient:
    """超管 + 自己名下的账号 1（含课程、任务点、事件）。"""
    seed(settings, owner_user_id=1)
    return logged_in


# --------------------------------------------------------------------------- #
# HTTP 端点
# --------------------------------------------------------------------------- #
def test_pause_checkpoint_resume_roundtrip(seeded: TestClient, settings: Settings) -> None:
    """pause → pause_requested →（运行器）paused → resume → 检查点消费 → running。"""
    paused = seeded.post("/api/study/pause", json={"account_id": 1})

    assert paused.status_code == 200, paused.text
    body = paused.json()
    assert body["state"] == RUN_PAUSE_REQUESTED
    assert body["note"], "状态说明要随响应返回（前端直接展示）"
    assert body["requested_at"]

    listed = seeded.get("/api/study/runs", params={"account_id": 1}).json()
    assert [item["state"] for item in listed] == [RUN_PAUSE_REQUESTED]

    assert checkpoint(settings, 1) == f"pause:{RUN_PAUSED}"
    assert seeded.get("/api/study/runs", params={"account_id": 1}).json()[0]["state"] == RUN_PAUSED

    resumed = seeded.post("/api/study/resume", json={"account_id": 1})
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["state"] == RUN_RESUME_REQUESTED

    assert checkpoint(settings, 1).startswith("continue")
    assert seeded.get("/api/study/runs", params={"account_id": 1}).json()[0]["state"] == RUN_RUNNING


def test_invalid_transition_is_409_and_missing_account_is_404(seeded: TestClient) -> None:
    """没有暂停却要继续 → 409；账号不存在 → 404。"""
    conflict = seeded.post("/api/study/resume", json={"account_id": 1})

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "invalid_transition"
    assert seeded.post("/api/study/pause", json={"account_id": 999}).status_code == 404


def test_runs_lists_only_visible_accounts(logged_in: TestClient, settings: Settings) -> None:
    """管理员不指定账号 = 全部；普通用户只看到自己名下的（这里是空列表）。"""
    seed(settings, owner_user_id=1)
    response = logged_in.get("/api/study/runs")
    assert response.status_code == 200, response.text
    admin_runs = response.json()
    assert [item["account_id"] for item in admin_runs] == [1]
    assert admin_runs[0]["state"] == RUN_RUNNING, "没有控制行就是运行中"

    register(logged_in, "looker")
    grant(logged_in, "looker", "viewer")
    logged_in.post("/api/auth/login", json={"username": "looker", "secret": secret_of("passw0rd")})

    assert logged_in.get("/api/study/runs").json() == []
    assert logged_in.get("/api/study/runs", params={"account_id": 1}).status_code == 404


def test_viewer_reads_state_but_cannot_control(
    client: TestClient, registered, settings: Settings
) -> None:
    """viewer 有 ``study:record:read``（能读状态）但没有 ``study:plan:control``。"""
    seed(settings, owner_user_id=1)
    register(client, "looker")
    grant(client, "looker", "viewer")
    client.post("/api/auth/login", json={"username": "looker", "secret": secret_of("passw0rd")})

    assert client.get("/api/study/runs", params={"account_id": 1}).status_code == 404  # 越权 404
    for path in ("/api/study/pause", "/api/study/resume"):
        response = client.post(path, json={"account_id": 1})
        assert response.status_code == 403, path
        assert response.json()["error"]["detail"]["missing"] == ["study:plan:control"]


def test_cross_account_control_is_404(client: TestClient, registered, settings: Settings) -> None:
    """别人的账号：不指定账号看不出（404），直接按 id 操作也是 404。"""
    seed(settings, owner_user_id=1)
    register(client, "looker")
    grant(client, "looker", "operator")
    client.post("/api/auth/login", json={"username": "looker", "secret": secret_of("passw0rd")})

    # operator 有 study:plan:control，但账号 1 不属于他 → 与不存在同处理
    assert client.post("/api/study/pause", json={"account_id": 1}).status_code == 404
    assert client.get("/api/study/runs", params={"account_id": 1}).status_code == 404


# --------------------------------------------------------------------------- #
# CLI：reset / logs / pause / resume
# --------------------------------------------------------------------------- #
def test_cli_reset_requeues_without_deleting(env: None, settings: Settings) -> None:
    """``cx study reset`` 把任务点打回 queued，课程 / 账号 / 事件记录一条不少。"""
    seed(settings)

    result = CliRunner().invoke(cli, ["study", "reset", "--account", "1"])

    assert result.exit_code == 0, result.output
    assert "已打回" in result.output and "3" in result.output
    state = snapshot(settings)
    assert state["points"] == [("p1", "queued", 0.0), ("p2", "queued", 0.0), ("p3", "queued", 0.0)]
    assert state["last_errors"] == ["", "", ""]
    assert state["retries"] == [0, 0, 0]
    assert state["courses"] == 2 and state["accounts"] == 1 and state["events"] == 3


def test_cli_reset_can_scope_to_one_course(env: None, settings: Settings) -> None:
    """``--course`` 只打回那门课。"""
    seed(settings)

    result = CliRunner().invoke(cli, ["study", "reset", "--account", "1", "--course", "1"])

    assert result.exit_code == 0, result.output
    state = snapshot(settings)
    assert state["points"] == [("p1", "queued", 0.0), ("p2", "queued", 0.0), ("p3", "done", 30.0)]


def test_cli_logs_are_newest_first_and_limited(env: None, settings: Settings) -> None:
    """``cx study logs`` 倒序输出，``--limit`` 生效。"""
    seed(settings)

    result = CliRunner().invoke(cli, ["study", "logs", "--account", "1", "--limit", "2"])

    assert result.exit_code == 0, result.output
    assert "最新在前" in result.output
    assert result.output.index("e2") < result.output.index("e1"), "最新的排在最上面"
    assert "e0" not in result.output, "limit=2 不该出现第三条"


def test_cli_pause_resume_and_errors(env: None, settings: Settings) -> None:
    """pause/resume 只写控制行；账号不存在或转移非法时给出人话并返回非零。"""
    seed(settings)
    runner = CliRunner()

    paused = runner.invoke(cli, ["study", "pause", "--account", "1"])
    assert paused.exit_code == 0, paused.output
    assert RUN_PAUSE_REQUESTED in paused.output

    # 运行器消费掉暂停请求 → paused；再点继续才是"继续"
    assert checkpoint(settings, 1) == f"pause:{RUN_PAUSED}"
    resumed = runner.invoke(cli, ["study", "resume", "--account", "1"])
    assert resumed.exit_code == 0, resumed.output
    assert RUN_RESUME_REQUESTED in resumed.output

    missing = runner.invoke(cli, ["study", "pause", "--account", "42"])
    assert missing.exit_code == 1
    assert "不存在" in missing.output

    # 继续请求已被消费 → 再次 resume 是非法转移（没有暂停可继续）
    assert checkpoint(settings, 1).startswith("continue")
    illegal = runner.invoke(cli, ["study", "resume", "--account", "1"])
    assert illegal.exit_code == 1
    assert "不能继续" in illegal.output
