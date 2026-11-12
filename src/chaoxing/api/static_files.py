"""托管前端产物（同源部署 + SPA fallback）。

三条规则：

* **产物不存在就优雅降级** —— 只提供 API 并打一条清晰的提示，而不是启动失败。
  开发期（`pnpm dev`）本来就不需要托管构建产物；
* **`/api` 绝不被通吃** —— SPA 的 catch-all 只兜非 API 路径，否则一个拼错的接口
  会返回 `index.html`（前端拿到 200 却解析不出 JSON，排查方向全被带偏）；
* **`/assets` 用静态目录挂载** —— 带哈希文件名的资源走静态文件服务（可缓存），
  不需要每请求过一次 Python 路由。
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

log = logging.getLogger(__name__)

#: API 前缀（catch-all 必须避开它）
API_PREFIX = "/api"

#: SPA fallback 的路由名：权限元测试据此把它认成"静态托管"而不是业务接口
SPA_ROUTE_NAME = "spa_fallback"

#: 内容哈希资源的缓存策略：文件名随内容变，可以永久缓存（一年，按 RFC 惯例）
_ONE_YEAR_SECONDS = 365 * 24 * 3600
IMMUTABLE_CACHE = f"public, max-age={_ONE_YEAR_SECONDS}, immutable"

#: 索引页的缓存策略：每次回源校验 —— 否则重发布后旧 index 会指着已删掉的哈希资源
INDEX_CACHE = "no-cache"


class HashedAssets(StaticFiles):
    """``/assets/*`` 的静态服务：vite 产物带内容哈希，可以永久缓存。

    Starlette 的 ``StaticFiles`` 不给自定义响应头，所以这里覆写 ``get_response``。
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["cache-control"] = IMMUTABLE_CACHE
        return response


def mount_frontend(app: FastAPI, dist: Path) -> bool:
    """把前端产物挂到应用上。

    :param app: FastAPI 应用
    :param dist: 构建产物目录（内含 ``index.html``）
    :returns: 是否完成挂载（目录不存在或没有 index.html 时返回 ``False``）
    """
    index = dist / "index.html"
    if not index.exists():
        log.warning(
            "未找到前端产物（%s）：本进程只提供 API。要一起托管请先在 web/ 下执行 `pnpm build`",
            index,
        )
        return False

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", HashedAssets(directory=assets), name="assets")

    @app.get(
        "/{full_path:path}",
        name=SPA_ROUTE_NAME,
        include_in_schema=False,
    )
    async def spa_fallback(full_path: str) -> Response:
        """SPA fallback：非 API 路径一律回 ``index.html``，交给前端路由处理。

        返回类型标注成 :class:`~starlette.responses.Response`：FastAPI 会把
        联合类型（``FileResponse | JSONResponse``）当成响应模型去校验，直接报错。
        """
        api_root = API_PREFIX.strip("/")
        if full_path == api_root or full_path.startswith(f"{api_root}/"):
            # 拼错的接口要**明确 404**，不能返回 HTML —— 否则前端拿到 200 却解析不出 JSON
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "not_found", "message": f"接口不存在：/{full_path}"}},
            )
        target = dist / full_path
        if full_path and target.is_file():
            # 根目录下的其他静态文件（favicon 等）直接给文件
            return FileResponse(target)
        if Path(full_path).suffix:
            # 看起来是个文件却不存在 → 404。回 index.html 会让浏览器把 HTML 当 JS/CSS 解析，
            # 到时现象是"资源加载了但语法错误"，排查方向会被彻底带偏
            return Response(status_code=404)
        return FileResponse(index, headers={"Cache-Control": INDEX_CACHE})

    log.info("已托管前端产物：%s", dist)
    return True
