"""前端产物托管与 SPA fallback。

来源：同源部署的三条纪律 ——
① 产物不存在时**优雅降级**（只提供 API 并提示），而不是启动失败；
② `/api` **绝不被通吃**：一个拼错的接口若返回 `index.html`，前端拿到 200 却解析不出 JSON，
   排查方向会被彻底带偏；
③ 带哈希文件名的资源走静态目录（可缓存），不每请求过一次 Python 路由。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaoxing.api.app import create_app
from chaoxing.config import AppSettings, Settings
from chaoxing.storage.engine import create_all, create_engine


def make_dist(root: Path) -> Path:
    """造一份最小可用的前端产物。"""
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text("<!doctype html><div id=root>SPA</div>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    return dist


def build_client(tmp_path: Path, *, with_dist: bool) -> TestClient:
    """按"有没有产物"构造客户端。"""
    dist = make_dist(tmp_path) if with_dist else tmp_path / "missing-dist"
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        secret_key="A" * 43 + "=",
        app=AppSettings(data_dir=tmp_path, web_dist_dir=dist),
    )
    engine = create_engine(settings.db_path)
    asyncio.run(_create(engine))
    return TestClient(create_app(settings))


async def _create(engine: object) -> None:
    await create_all(engine)  # type: ignore[arg-type]
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.fixture
def spa_client(tmp_path: Path) -> TestClient:
    """带产物的客户端。"""
    with build_client(tmp_path, with_dist=True) as client:
        yield client


@pytest.fixture
def api_only_client(tmp_path: Path) -> TestClient:
    """没有产物的客户端（应优雅降级）。"""
    with build_client(tmp_path, with_dist=False) as client:
        yield client


def test_root_serves_index(spa_client: TestClient) -> None:
    """根路径返回 SPA 入口。"""
    response = spa_client.get("/")

    assert response.status_code == 200
    assert "SPA" in response.text


def test_unknown_frontend_route_returns_index(spa_client: TestClient) -> None:
    """前端路由（如 /accounts/3）也回 index.html —— 刷新页面不会 404。"""
    response = spa_client.get("/accounts/3")

    assert response.status_code == 200
    assert "SPA" in response.text


def test_api_paths_are_not_swallowed(spa_client: TestClient) -> None:
    """**拼错的接口必须明确 404 JSON**，不能返回 HTML。"""
    response = spa_client.get("/api/definitely-not-here")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "not_found"


def test_real_api_still_works(spa_client: TestClient) -> None:
    """挂在最前面的 API 路由没被 catch-all 遮住。"""
    response = spa_client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_assets_are_served_statically(spa_client: TestClient) -> None:
    """带哈希文件名的资源走静态目录（可缓存）。"""
    response = spa_client.get("/assets/app.js")

    assert response.status_code == 200
    assert "console.log" in response.text


def test_single_page_app_does_not_break_openapi(spa_client: TestClient) -> None:
    """接口文档照常可用（catch-all 不吞 /api/docs）。"""
    assert spa_client.get("/api/openapi.json").status_code == 200


def test_missing_dist_degrades_gracefully(api_only_client: TestClient) -> None:
    """没有产物时只提供 API —— 启动成功、接口正常，根路径没有 SPA。"""
    assert api_only_client.get("/api/health").status_code == 200

    response = api_only_client.get("/")

    assert response.status_code == 404, "没有产物时根路径不该返回任何页面"


def test_mount_frontend_reports_state(tmp_path: Path) -> None:
    """挂载函数要如实返回"挂上了没有"（调用方据此打日志）。"""
    from chaoxing.api.static_files import mount_frontend

    app = FastAPI()
    assert mount_frontend(app, make_dist(tmp_path)) is True
    assert mount_frontend(app, tmp_path / "nope") is False


# --------------------------------------------------------------------------- #
# 托管边界（2026-10-11 实测出的三条，见计划 Task 6）
# --------------------------------------------------------------------------- #


def test_spa_route_starting_with_api_is_not_swallowed(spa_client: TestClient) -> None:
    """`/api-keys` 是前端路由，不是接口 —— 前缀必须按**路径段**判断。

    实测过的现状：`startswith("api")` 把 `/api-keys` 当成拼错的接口，回了 JSON 404。
    """
    response = spa_client.get("/api-keys")

    assert response.status_code == 200
    assert "SPA" in response.text


def test_missing_root_asset_is_404_not_index(spa_client: TestClient) -> None:
    """缺文件回 HTML 会让浏览器把它当资源解析 —— 必须 404。

    （`/assets/*` 的缺失由 StaticFiles 自己 404；这条盯的是根目录下的静态文件。）
    """
    assert spa_client.get("/favicon.svg").status_code == 404


def test_cache_headers_split_index_and_hashed_assets(spa_client: TestClient) -> None:
    """索引每次回源校验；内容哈希资源永久缓存（重发布后旧 index 不会指着已删资源）。"""
    index = spa_client.get("/")
    asset = spa_client.get("/assets/app.js")

    assert "no-cache" in index.headers.get("cache-control", "")
    assert "immutable" in asset.headers.get("cache-control", "")
