"""系统域：端到端体检 / 端点注册表 / 任务记录 / 日志尾部。

来源：计划 §5.11 的 system 与 logs 路由组。这些是**运维视角**的只读端点，
所以权限点是 `system:doctor` / `system:task` / `system:logs`（`admin` 角色持有）。

任务取消的状态机在服务层（`JobService.cancel`）：只允许 queued / running，
其余状态报 400 —— 假装成功比报错更糟。
"""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from chaoxing.config import Settings
from chaoxing.core.container import Container
from chaoxing.storage.models import Job


def _seed_job(client: TestClient, *, status: str = "queued") -> int:
    """直接造一条任务记录。"""
    container: Container = client.app.state.container  # type: ignore[attr-defined]

    async def prepare() -> int:
        async with container.uow.begin() as db:
            job = Job(kind="study.run", status=status)
            db.add(job)
            await db.flush()
            return job.id

    return asyncio.run(prepare())


def test_doctor_returns_probe_rows(logged_in: TestClient) -> None:
    """体检端点返回探针结果（与 `cx doctor --full` 同一套）。"""
    response = logged_in.get("/api/system/doctor")

    assert response.status_code == 200, response.text
    names = {row["name"] for row in response.json()}
    assert {"container", "api/health"} <= names


def test_endpoints_lists_registry(logged_in: TestClient) -> None:
    """端点注册表可读，且与 `client/endpoints` 同源。"""
    response = logged_in.get("/api/system/endpoints")

    assert response.status_code == 200, response.text
    names = {row["name"] for row in response.json()}
    assert {"video_heartbeat", "quiz_submit"} <= names


def test_tasks_list_and_cancel(logged_in: TestClient) -> None:
    """任务：列表 → 取消 → 再取消报 400（状态机）。"""
    job_id = _seed_job(logged_in)

    listed = logged_in.get("/api/system/tasks")
    assert listed.status_code == 200, listed.text
    assert any(row["id"] == job_id and row["status"] == "queued" for row in listed.json())

    cancelled = logged_in.post(f"/api/system/tasks/{job_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"

    again = logged_in.post(f"/api/system/tasks/{job_id}/cancel")
    assert again.status_code == 400
    assert again.json()["error"]["code"] == "job_not_cancellable"


def test_task_cancel_missing_is_404(logged_in: TestClient) -> None:
    """取消不存在的任务 → 404。"""
    assert logged_in.post("/api/system/tasks/9999/cancel").status_code == 404


def test_logs_tail_and_level_filter(
    logged_in: TestClient, settings: Settings, tmp_path: object
) -> None:
    """日志端点读尾部并按级别过滤。"""
    log_dir = settings.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        {"level": "info", "logger": "test", "event": "普通一行", "timestamp": "t1"},
        {"level": "warning", "logger": "test", "event": "警告一行", "timestamp": "t2"},
        {"level": "error", "logger": "test", "event": "错误一行", "timestamp": "t3"},
    ]
    (log_dir / "app.log").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    filtered = logged_in.get("/api/logs", params={"min_level": "WARNING"}).json()

    events = [row["event"] for row in filtered]
    assert "警告一行" in events
    assert "错误一行" in events
    assert "普通一行" not in events


def test_system_endpoints_require_admin_permissions(client: TestClient, registered) -> None:
    """运维只读端点对普通用户关闭（没有 system:* 节点就是 403）。"""
    import hashlib

    nonce = client.get("/api/auth/challenge", params={"scene": "register"}).json()["nonce"]
    client.post(
        "/api/auth/register",
        json={
            "username": "looker",
            "secret": hashlib.sha256(b"passw0rd").hexdigest(),
            "nonce": nonce,
        },
    )
    client.post(
        "/api/auth/login",
        json={"username": "looker", "secret": hashlib.sha256(b"passw0rd").hexdigest()},
    )

    assert client.get("/api/system/doctor").status_code == 403
    assert client.get("/api/system/tasks").status_code == 403
    assert client.get("/api/logs").status_code == 403
