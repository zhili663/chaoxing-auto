"""``cx doctor``：项目唯一的体检入口。

四个模式（见 ``docs/REFACTOR_PLAN.md`` §5.9）::

    默认      环境与依赖 / 配置 / 数据库
    --full    追加端到端体检：前端产物能否被托管、SSE 连通、真实账号抽检
    --raw     额外输出原始探针结果（命令回显、状态码）
    --fix     自动修复可安全修复的项（例如缺失的 SECRET_KEY）

退出码：任何 FAIL → 1，否则 0，因此可直接当 CI 门禁用。

设计约束：原先散落在 ``tools/`` 里的"检查类""网页测试类"脚本全部收口在这里
（``check_deploy`` / ``verify_config`` / ``live_test`` / ``preview_page``），
CLI 里不再出现 ``check-*`` / ``test-*`` 之类的子命令。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum

import click
from rich.console import Console
from rich.table import Table

from chaoxing import __version__
from chaoxing.config import BASE_DIR, Settings, get_settings, reload_settings
from chaoxing.core.container import Container


class Status(StrEnum):
    """单项检查的结论。"""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass(slots=True)
class CheckResult:
    """一条体检结果。

    :param name: 检查项名称（稳定标识，便于 CI 断言）
    :param status: 结论
    :param detail: 人类可读的一句话详情
    :param hint: 建议动作（仅在 WARN / FAIL 时展示）
    :param raw: ``--raw`` 时展示的原始输出
    """

    name: str
    status: Status
    detail: str = ""
    hint: str = ""
    raw: str = ""


_ICON = {
    Status.PASS: "[green]✔[/]",
    Status.WARN: "[yellow]·[/]",
    Status.FAIL: "[red]✘[/]",
    Status.SKIP: "[dim]–[/]",
}

# --------------------------------------------------------------------------- #
# 单项检查
# --------------------------------------------------------------------------- #


def check_python() -> CheckResult:
    """解释器版本必须 >= 3.12（项目用了 3.12 的语法与标准库行为）。"""
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    # 保留这道检查：目标版本是 3.12，但用户完全可能用错解释器，
    # 这里要给出"怎么装对版本"的提示，而不是让它去解析一堆语法错误。
    if sys.version_info[:2] >= (3, 12):  # noqa: UP036
        return CheckResult("python", Status.PASS, f"{version} @ {sys.executable}")
    return CheckResult(
        "python",
        Status.FAIL,
        f"{version} 过低",
        hint="需要 >= 3.12；可执行 `uv python install 3.12` 交给 uv 托管。",
    )


def check_package() -> CheckResult:
    """本包可导入（安装方式是否正确：uv 以 editable 方式装入 .venv）。"""
    return CheckResult("package", Status.PASS, f"chaoxing {__version__}（importable）")


def check_lock() -> CheckResult:
    """依赖漂移检查：``pyproject.toml`` 是否与 ``uv.lock`` 一致。

    这是"禁止手写 pyproject"这条规矩的机器判据：有人手改 dependencies
    或者漏了 ``uv lock``，这里就会亮红灯。
    """
    pyproject = BASE_DIR / "pyproject.toml"
    lockfile = BASE_DIR / "uv.lock"
    if not pyproject.exists():
        return CheckResult("lock", Status.FAIL, "pyproject.toml 缺失")
    if not lockfile.exists():
        return CheckResult("lock", Status.FAIL, "uv.lock 缺失", hint="执行 `uv lock` 生成并入库。")

    uv = shutil.which("uv")
    if uv is None:
        fresh = lockfile.stat().st_mtime >= pyproject.stat().st_mtime
        return CheckResult(
            "lock",
            Status.WARN if fresh else Status.FAIL,
            "uv 不在 PATH，退化为 mtime 比较",
            hint="把 uv 放进 PATH 后可做精确检查（`uv lock --check`）。",
        )

    try:
        proc = subprocess.run(
            [uv, "lock", "--check"],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - 环境异常
        return CheckResult("lock", Status.WARN, f"无法执行 uv lock --check：{exc}")

    raw = f"$ uv lock --check\n{proc.stdout}{proc.stderr}".strip()
    if proc.returncode == 0:
        return CheckResult("lock", Status.PASS, "pyproject 与 uv.lock 一致", raw=raw)
    return CheckResult(
        "lock",
        Status.FAIL,
        "依赖漂移：pyproject 被手工改过，或未重新锁定",
        hint="用 `uv add/remove <pkg>` 改依赖，然后 `uv lock`；禁止手写 dependencies。",
        raw=raw,
    )


def check_config(settings: Settings) -> CheckResult:
    """配置能解析，并回报几个关键项，避免"跑起来才发现配错"。"""
    app, cx, ai = settings.app, settings.chaoxing, settings.ai
    detail = (
        f"host={app.host}:{app.port} · rate={cx.video_rate}x · "
        f"心跳 {cx.heartbeat_interval_min:g}~{cx.heartbeat_interval_max:g}s · "
        f"并发={cx.max_account_concurrency} · ai={ai.provider}/{ai.model}"
    )
    return CheckResult("config", Status.PASS, detail)


def check_exposure(settings: Settings) -> CheckResult:
    """危险暴露：对外监听 + 无鉴权 = 拒绝启动（沿用 legacy 的安全闸门）。"""
    if settings.app.insecure_exposure:
        return CheckResult(
            "exposure",
            Status.FAIL,
            "对外监听且未设置 API_TOKEN",
            hint=settings.app.exposure_help(),
        )
    if settings.app.listens_publicly and settings.app.allow_open_access:
        return CheckResult(
            "exposure",
            Status.WARN,
            "对外监听且显式关闭鉴权（ALLOW_OPEN_ACCESS=true）",
            hint="确认该端口只对可信内网开放。",
        )
    return CheckResult("exposure", Status.PASS, f"监听 {settings.app.host}，鉴权策略正常")


def check_secret_key(settings: Settings) -> CheckResult:
    """账号凭据的对称密钥是否就绪（缺了就不能加密落库）。"""
    if settings.secret_key:
        return CheckResult("secret_key", Status.PASS, "已设置（Fernet key，长度已隐藏）")
    return CheckResult(
        "secret_key",
        Status.WARN,
        "未设置",
        hint="执行 `cx doctor --fix` 生成并写入 .env（不会覆盖已有值）。",
    )


def check_data_dir(settings: Settings) -> CheckResult:
    """数据目录必须可创建、可写（日志、SQLite、验证码图片都落在这里）。"""
    data_dir = settings.app.data_dir
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".doctor-probe"
        probe.write_text("ok", encoding="utf-8", newline="\n")
        probe.unlink()
    except OSError as exc:
        return CheckResult("data_dir", Status.FAIL, f"不可写：{exc}", hint=str(data_dir))
    return CheckResult("data_dir", Status.PASS, str(data_dir))


def check_database(settings: Settings) -> CheckResult:
    """SQLite 存储层在 P2 接入；此处只报告现状，不误报。"""
    db_path = settings.db_path
    if db_path.exists():
        size_kb = db_path.stat().st_size / 1024
        return CheckResult("database", Status.PASS, f"{db_path.name}（{size_kb:.0f} KB）")
    return CheckResult("database", Status.SKIP, "尚未创建（存储层 P2 接入，`cx db upgrade` 建库）")


def check_frontend() -> CheckResult:
    """前端产物是否就绪（就绪时由 API 同源托管）。"""
    dist = BASE_DIR / "web" / "dist" / "index.html"
    if dist.exists():
        return CheckResult("frontend", Status.PASS, str(dist))
    return CheckResult(
        "frontend", Status.SKIP, "web/dist 未构建（`cd web && pnpm build` 后由 API 托管）"
    )


async def _probe_app(settings: Settings) -> list[CheckResult]:
    """真正把应用跑起来探一遍（含 lifespan 里的容器装配）。

    走 ASGI 直连而不是起 uvicorn：**不占端口、不依赖已有服务**，
    因此 ``cx doctor --full`` 在任何时候都能跑，不会因为"服务没起"而失去意义。
    """
    from httpx import ASGITransport, AsyncClient

    from chaoxing.api.app import create_app

    results: list[CheckResult] = []
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        container = getattr(app.state, "container", None)
        if container is None:
            results.append(CheckResult("container", Status.FAIL, "lifespan 没建出容器"))
            return results
        subscribers = container.bus.stats()["subscribers"]
        results.append(
            CheckResult("container", Status.PASS, f"装配完成，事件订阅者 {subscribers} 个")
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://doctor") as client:
            health = await client.get("/api/health")
            ok = health.status_code == 200 and health.json().get("status") == "ok"
            results.append(
                CheckResult(
                    "api/health",
                    Status.PASS if ok else Status.FAIL,
                    f"HTTP {health.status_code} {health.text[:80]}",
                )
            )

            # SPA fallback：前端产物在时根路径应回 HTML
            index = settings.web_dist / "index.html"
            root = await client.get("/")
            if index.exists():
                good = root.status_code == 200 and "<" in root.text
                results.append(
                    CheckResult(
                        "spa",
                        Status.PASS if good else Status.FAIL,
                        f"根路径 HTTP {root.status_code}（产物已托管）",
                    )
                )
            else:
                results.append(CheckResult("spa", Status.SKIP, "没有前端产物，只提供 API"))

            # SSE：未登录时**必须 401** —— 401 说明路由在、鉴权也生效；
            # 404/500 才是问题（端点没挂上或鉴权链路炸了）
            events = await client.get("/api/events")
            good = events.status_code == 401
            results.append(
                CheckResult(
                    "sse",
                    Status.PASS if good else Status.FAIL,
                    f"/api/events HTTP {events.status_code}"
                    + ("（未登录即 401，端点与鉴权都在位）" if good else "（预期 401）"),
                )
            )

        results.append(await _probe_account_session(container))

    return results


async def _probe_account_session(container: Container) -> CheckResult:
    """真实账号登录态抽检（计划 §5.9 里 ``--full`` 的第四项语义）。

    这一步会**真的打一次学习通**，因此默认只在 ``--full`` 下跑：
    它回答的是"这个环境现在真的能刷课吗"，而不是"配置看起来对不对"。

    没有账号、或账号都没登录态时标 SKIP —— 那是"还没准备"，不是"体检失败"。
    """
    from chaoxing.client import LoginApi

    accounts = await container.accounts.list_accounts()
    if not accounts:
        return CheckResult(
            "accounts", Status.SKIP, "库里还没有账号（先建号并登录，`cx auth login`）"
        )

    logged = [item for item in accounts if item.cookie_enc]
    if not logged:
        return CheckResult(
            "accounts", Status.SKIP, f"{len(accounts)} 个账号都还没有登录态（`cx auth login`）"
        )

    target = logged[0]
    if not container.registry.has(target.id):
        return CheckResult(
            "accounts",
            Status.SKIP,
            f"账号 {target.username} 有会话快照，但本次没能从库里恢复（看看 cx doctor --raw）",
        )

    try:
        valid = await LoginApi(container.transport).verify_session(
            container.registry.get(target.id)
        )
    except Exception as exc:
        return CheckResult(
            "accounts",
            Status.SKIP,
            f"账号 {target.username} 抽检遇到网络问题：{type(exc).__name__}",
        )

    if valid:
        return CheckResult(
            "accounts", Status.PASS, f"账号 {target.username} 登录态有效（真实请求已校验）"
        )
    return CheckResult(
        "accounts", Status.FAIL, f"账号 {target.username} 登录态已失效，需要重新登录"
    )


async def probe_app(settings: Settings) -> list[CheckResult]:
    """端到端探针的**异步入口**（供已经在事件循环里的调用方使用，如 `/system/doctor`）。

    :param settings: 配置
    :returns: 各项探针结果
    """
    return await _probe_app(settings)


def check_runtime_endpoints(settings: Settings) -> list[CheckResult]:
    """端到端探针：真跑一遍应用（容器装配 / 健康检查 / SPA 托管 / SSE 端点）。"""
    import asyncio

    try:
        return asyncio.run(probe_app(settings))
    except Exception as exc:
        return [CheckResult("e2e", Status.FAIL, f"探针执行失败：{type(exc).__name__}: {exc}")]


def run_checks(settings: Settings, *, full: bool = False) -> list[CheckResult]:
    """执行全部检查。

    :param settings: 已解析的配置
    :param full: 是否执行端到端体检
    :returns: 按执行顺序排列的结果列表
    """
    results = [
        check_python(),
        check_package(),
        check_lock(),
        check_config(settings),
        check_exposure(settings),
        check_secret_key(settings),
        check_data_dir(settings),
        check_database(settings),
    ]
    results.append(check_frontend())
    if full:
        results.extend(check_runtime_endpoints(settings))
    return results


# --------------------------------------------------------------------------- #
# --fix
# --------------------------------------------------------------------------- #


def apply_fixes(settings: Settings) -> list[str]:
    """执行可安全自动修复的项，返回"做了什么"的人类可读说明。

    当前只有一项：缺 ``SECRET_KEY`` 时生成 Fernet key 写入 ``.env``。
    绝不覆盖已有值 —— 旧密钥一旦被替换，已加密的账号凭据就解不开了。

    :param settings: 已解析的配置
    :returns: 修复动作说明列表（空表示无需修复）
    """
    applied: list[str] = []
    if settings.secret_key:
        return applied

    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    env_path = BASE_DIR / ".env"

    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
        replaced = False
        for index, line in enumerate(lines):
            if line.strip().upper().startswith("SECRET_KEY="):
                lines[index] = f"SECRET_KEY={key}"
                replaced = True
                break
        if not replaced:
            lines.append(f"SECRET_KEY={key}")
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    else:
        env_path.write_text(f"SECRET_KEY={key}\n", encoding="utf-8", newline="\n")

    applied.append(f"已生成 SECRET_KEY 并写入 {env_path}")
    return applied


# --------------------------------------------------------------------------- #
# 渲染与命令
# --------------------------------------------------------------------------- #


def render(console: Console, results: list[CheckResult], *, raw: bool = False) -> None:
    """把检查结果渲染成表格（+ 建议 + 可选原始输出）。

    :param console: rich 控制台
    :param results: 检查结果
    :param raw: 是否追加原始输出
    """
    table = Table(title=f"cx doctor · chaoxing {__version__}", title_justify="left")
    table.add_column("", width=2, no_wrap=True)
    table.add_column("项目", style="bold", no_wrap=True)
    table.add_column("结论", width=5, no_wrap=True)
    table.add_column("详情", overflow="fold")
    for item in results:
        table.add_row(_ICON[item.status], item.name, item.status.value, item.detail)
    console.print(table)

    hints = [r for r in results if r.hint and r.status in (Status.WARN, Status.FAIL)]
    if hints:
        console.print("\n[bold]建议[/]")
        for item in hints:
            console.print(f"  [bold]{item.name}[/]：{item.hint}")

    if raw:
        console.print("\n[bold]原始输出[/]")
        for item in results:
            if item.raw:
                console.print(f"[dim]{item.name}[/]\n{item.raw}")


@click.command()
@click.option("--full", is_flag=True, help="追加端到端体检：前端产物 / SSE 连通 / 账号抽检")
@click.option("--raw", is_flag=True, help="输出原始探针结果")
@click.option("--fix", is_flag=True, help="自动修复可安全修复的项（缺 SECRET_KEY 等）")
def doctor(full: bool, raw: bool, fix: bool) -> None:
    """体检：环境与依赖 / 配置 / 数据库（`--full` 追加端到端）。"""
    console = Console()
    try:
        settings = get_settings()
    except Exception as exc:
        console.print(f"[red]配置解析失败[/] {exc}")
        raise SystemExit(1) from exc

    if fix:
        fixes = apply_fixes(settings)
        for message in fixes:
            console.print(f"[green]已修复[/] {message}")
        if fixes:
            settings = reload_settings()

    results = run_checks(settings, full=full)
    render(console, results, raw=raw)
    raise SystemExit(1 if any(r.status is Status.FAIL for r in results) else 0)
