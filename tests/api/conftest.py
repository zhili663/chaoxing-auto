"""API 测试夹具：每个用例一套临时库 + 自己的 app。

测试里**不用** ``monkeypatch`` 打补丁 —— 而是给 ``create_app`` 注入一份指向临时目录的
配置，走的就是生产那条装配路径（lifespan → 容器 → 路由），因此测的是真东西。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaoxing.api.app import create_app
from chaoxing.config import AppSettings, Settings
from chaoxing.storage.engine import create_all, create_engine


def client_secret(password: str) -> str:
    """前端口径的口令：``sha256(password)``（测试全程只用哈希值）。"""
    import hashlib

    return hashlib.sha256(password.encode()).hexdigest()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """指向临时目录的完整配置。"""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        secret_key="A" * 43 + "=",
        app=AppSettings(data_dir=tmp_path, allow_registration=True, secure_cookies=False),
    )


async def _prepare_database(settings: Settings) -> None:
    """建表（生产走 Alembic，测试用 create_all 直连同一套模型）。"""
    engine = create_engine(settings.db_path)
    try:
        await create_all(engine)
    finally:
        await engine.dispose()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    """建好表的应用实例。"""
    asyncio.run(_prepare_database(settings))
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """带 lifespan 的测试客户端（``with`` 会真正跑启动与关闭）。"""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def registered(client: TestClient) -> dict[str, str]:
    """注册首个用户（自动成为超管）并返回登录信息。"""
    secret = client_secret("passw0rd")
    response = client.post(
        "/api/auth/register",
        json={"username": "admin", "secret": secret, "display_name": "管理员"},
    )
    assert response.status_code == 201, response.text
    return {"username": "admin", "secret": secret}


@pytest.fixture
def logged_in(client: TestClient, registered: dict[str, str]) -> TestClient:
    """已登录的客户端（Cookie 已在 jar 里）。"""
    response = client.post("/api/auth/login", json=registered)
    assert response.status_code == 200, response.text
    return client
