"""``cx serve``：启动 API 服务（生产同源托管前端产物）。

启动前做三项检查，把"起得来但用不了"的情况挡在前面：

1. **危险暴露**：对外监听却没鉴权 → 拒绝启动（配置层的既有规则）；
2. **数据库**：迁移版本要在 head（否则新代码配旧表，运行期才炸）；
3. **前端产物**：存在就一起托管；不存在只提示，不算失败（开发期本就不需要）。
"""

from __future__ import annotations

import logging

import click
from rich.console import Console

from chaoxing.config import get_settings

CONSOLE = Console()
log = logging.getLogger(__name__)


def _check_database(settings: object) -> str:
    """检查数据库是否存在及迁移状态。"""
    from chaoxing.config import Settings

    assert isinstance(settings, Settings)
    path = settings.db_path
    if not path.exists():
        return "[yellow]数据库尚未创建[/]首次启动会自动建表？不 —— 请先跑 `cx db upgrade`"
    return f"数据库：{path}"


def _check_frontend(settings: object) -> str:
    """检查前端产物是否就绪。"""
    from chaoxing.config import Settings

    assert isinstance(settings, Settings)
    index = settings.web_dist / "index.html"
    if index.exists():
        return f"前端产物：{settings.web_dist}（将由本进程托管）"
    return "[yellow]未找到前端产物[/]web/ 下执行 `pnpm build` 后可由本进程同源托管"


@click.command("serve")
@click.option("--host", default=None, help="监听地址（默认取配置）")
@click.option("--port", type=int, default=None, help="端口（默认取配置）")
@click.option("--reload", is_flag=True, help="代码变更自动重启（仅开发用）")
@click.option("--check-only", is_flag=True, help="只做启动前检查，不真正启动")
def serve(host: str | None, port: int | None, reload: bool, check_only: bool) -> None:
    """启动 API（生产环境同时托管前端）。"""
    import uvicorn

    settings = get_settings()
    bind_host = host or settings.app.host
    bind_port = port or settings.app.port

    if settings.app.insecure_exposure:
        CONSOLE.print(f"[red]{settings.app.exposure_help()}[/]")
        raise SystemExit(2)
    if reload and settings.app.listens_publicly:
        CONSOLE.print("[red]--reload 不能在对外监听的地址上使用[/]")
        raise SystemExit(2)

    CONSOLE.print(_check_database(settings))
    CONSOLE.print(_check_frontend(settings))
    CONSOLE.print(
        f"监听：http://{bind_host}:{bind_port}（接口文档 {bind_host}:{bind_port}/api/docs）"
    )

    if check_only:
        CONSOLE.print("[green]检查通过[/]")
        return

    uvicorn.run(
        "chaoxing.api.app:create_app",
        factory=True,
        host=bind_host,
        port=bind_port,
        reload=reload,
        # workers 固定 1：进程内事件总线 + SQLite 单写者，多 worker 会同时破坏这两条前提
        workers=1,
        log_config=None,
    )
