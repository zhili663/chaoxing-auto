"""仪表盘「并发刷课」模块的数据端点。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_active_study_empty_returns_concurrency(logged_in: TestClient) -> None:
    """没有 running 会话时返回空列表，但并发上限要带上（模块要展示“常开”）。"""
    response = logged_in.get("/api/study/active")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accounts"] == []
    assert body["concurrency"] >= 1
