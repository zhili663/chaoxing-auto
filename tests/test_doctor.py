"""``cx doctor`` 的行为边界。

关注两件事（都是真会出事的）：

1. 体检本身必须能跑、退出码语义正确（CI 直接拿它当门禁）；
2. ``--fix`` 生成密钥时**绝不能覆盖已有 SECRET_KEY** —— 旧密钥一换，
   已经加密入库的账号凭据就永久解不开了。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from chaoxing.cli import doctor as doctor_module
from chaoxing.cli.doctor import Status, apply_fixes, check_lock, run_checks
from chaoxing.cli.main import cli
from chaoxing.config import Settings


def _settings(**overrides: object) -> Settings:
    """不读 .env 的配置对象（避免受仓库里那份 legacy .env 影响）。"""
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_doctor_runs_and_exits_clean() -> None:
    """体检在正常仓库里应当直接通过（否则 CI 门禁没意义）。"""
    result = CliRunner().invoke(cli, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "lock" in result.output
    assert "FAIL" not in result.output


def test_doctor_full_mode_runs() -> None:
    """``--full`` 追加端到端项，并**真的把应用跑一遍**（不是标 SKIP 了事）。"""
    result = CliRunner().invoke(cli, ["doctor", "--full"])

    assert result.exit_code == 0, result.output
    for item in ("container", "api/health", "sse"):
        assert item in result.output, f"--full 缺少 {item} 探针"


def test_full_probe_checks_container_health_and_sse(tmp_path: Path) -> None:
    """端到端探针的四项：容器装配、健康检查、SPA 托管、SSE 端点与鉴权。

    走 ASGI 直连而不是起 uvicorn —— 所以它在"服务没起"时同样能跑，
    体检不会因为环境状态而失去意义。
    """
    import asyncio

    from chaoxing.cli.doctor import check_runtime_endpoints
    from chaoxing.storage.engine import create_all, create_engine

    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        secret_key="A" * 43 + "=",
        app={"data_dir": tmp_path, "web_dist_dir": tmp_path / "no-dist"},
    )

    # 探针会跑 lifespan（容器要读会话表）—— 空库上必须先建表，
    # 这也正好说明真实的 `cx doctor --full` 依赖"库已迁移"这个前提。
    async def prepare() -> None:
        engine = create_engine(settings.db_path)
        try:
            await create_all(engine)
        finally:
            await engine.dispose()

    asyncio.run(prepare())

    results = check_runtime_endpoints(settings)
    by_name = {item.name: item for item in results}

    assert by_name["container"].status is Status.PASS
    assert by_name["api/health"].status is Status.PASS
    # 没有前端产物时该项标 SKIP（不是失败）—— 开发期本来就不需要构建
    assert by_name["spa"].status is Status.SKIP
    # SSE 未登录必须 401：401 说明路由在、鉴权也生效
    assert by_name["sse"].status is Status.PASS
    assert "401" in by_name["sse"].detail


def test_account_probe_skips_when_no_accounts(tmp_path: Path) -> None:
    """库里没有账号时，登录态抽检标 SKIP —— 那是"还没准备"，不是"体检失败"。"""
    import asyncio

    from chaoxing.cli.doctor import _probe_account_session
    from chaoxing.core.container import Container
    from chaoxing.storage.engine import create_all, create_engine

    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, secret_key="A" * 43 + "=", app={"data_dir": tmp_path}
    )

    async def run() -> object:
        engine = create_engine(settings.db_path)
        try:
            await create_all(engine)
        finally:
            await engine.dispose()
        container = await Container.create(settings)
        try:
            return await _probe_account_session(container)
        finally:
            await container.aclose()

    result = asyncio.run(run())

    assert result.status is Status.SKIP
    assert "没有账号" in result.detail


def test_account_probe_skips_when_account_has_no_session(tmp_path: Path) -> None:
    """有账号但没登录态时同样标 SKIP，并指出下一步怎么做。"""
    import asyncio

    from chaoxing.cli.doctor import _probe_account_session
    from chaoxing.core.container import Container
    from chaoxing.storage.engine import create_all, create_engine
    from chaoxing.storage.models import Account

    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, secret_key="A" * 43 + "=", app={"data_dir": tmp_path}
    )

    async def run() -> object:
        engine = create_engine(settings.db_path)
        try:
            await create_all(engine)
        finally:
            await engine.dispose()
        container = await Container.create(settings)
        try:
            async with container.uow.begin() as db:
                db.add(Account(username="13800000000"))
                await db.flush()
            return await _probe_account_session(container)
        finally:
            await container.aclose()

    result = asyncio.run(run())

    assert result.status is Status.SKIP
    assert "登录态" in result.detail
    assert "cx auth login" in result.detail


def test_full_probe_reports_failure_on_broken_database(tmp_path: Path) -> None:
    """库没迁移时要**如实报失败**，而不是让整条体检崩掉。"""
    from chaoxing.cli.doctor import check_runtime_endpoints

    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        secret_key="A" * 43 + "=",
        app={"data_dir": tmp_path / "empty"},
    )

    results = check_runtime_endpoints(settings)

    assert results, "探针至少该返回一条结果"
    assert any(item.status is Status.FAIL for item in results)
    assert "探针" in results[0].detail or "失败" in results[0].detail


def test_doctor_raw_mode_prints_probe_output() -> None:
    """``--raw`` 需要把原始探针结果打出来（排障时才不用二次重跑）。"""
    result = CliRunner().invoke(cli, ["doctor", "--raw"])

    assert result.exit_code == 0, result.output
    assert "原始输出" in result.output


def test_run_checks_contains_core_items() -> None:
    """核心检查项一个都不能少（回归保护：别被误删）。"""
    names = {item.name for item in run_checks(_settings())}

    assert {"python", "package", "lock", "config", "exposure", "data_dir"} <= names


def test_lock_check_reports_pass_on_clean_tree() -> None:
    """依赖未漂移时应当是 PASS（"禁止手写 pyproject"的机器判据）。"""
    result = check_lock()

    assert result.status is Status.PASS, f"{result.detail} / {result.raw}"


# --------------------------------------------------------------------------- #
# --fix：生成密钥，但绝不覆盖
# --------------------------------------------------------------------------- #


def test_apply_fixes_generates_key_into_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缺 SECRET_KEY 时生成 Fernet key 并写入 .env。"""
    env_file = tmp_path / ".env"
    env_file.write_text("APP__PORT=8000\n", encoding="utf-8")
    monkeypatch.setattr(doctor_module, "BASE_DIR", tmp_path)

    applied = apply_fixes(_settings())

    text = env_file.read_text(encoding="utf-8")
    assert applied and "SECRET_KEY=" in text
    assert text.startswith("APP__PORT=8000"), "原有配置行不能被破坏"


def test_apply_fixes_replaces_placeholder_empty_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``SECRET_KEY=`` 这种空占位行应当被就地填上，而不是追加第二行。"""
    env_file = tmp_path / ".env"
    env_file.write_text("SECRET_KEY=\n", encoding="utf-8")
    monkeypatch.setattr(doctor_module, "BASE_DIR", tmp_path)

    apply_fixes(_settings())

    lines = [line for line in env_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0].startswith("SECRET_KEY=")
    assert len(lines[0]) > len("SECRET_KEY=")


def test_apply_fixes_never_overwrites_existing_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """已有密钥时不得改动文件（覆盖 = 旧密文永久解不开）。"""
    env_file = tmp_path / ".env"
    original = "SECRET_KEY=keep-me-please\n"
    env_file.write_text(original, encoding="utf-8")
    monkeypatch.setattr(doctor_module, "BASE_DIR", tmp_path)

    applied = apply_fixes(_settings(secret_key="keep-me-please"))

    assert applied == []
    assert env_file.read_text(encoding="utf-8") == original
