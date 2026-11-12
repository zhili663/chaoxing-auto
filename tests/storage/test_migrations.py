"""迁移与模型的一致性。

抓的是最容易被忽略的一种事故：**改了模型却忘了生成迁移** —— 本地跑得好好的，
一到新环境建库就少表少列，而且报错往往晚到运行期。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from chaoxing.storage.base import Base

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "src" / "chaoxing" / "storage" / "migrations"
VERSIONS = MIGRATIONS / "versions"


def _migration_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(VERSIONS.glob("*.py")))


def test_at_least_one_migration_is_committed() -> None:
    """迁移脚本必须入库（别只躺在本地）。"""
    assert list(VERSIONS.glob("*.py")), "没有任何迁移脚本"


def test_every_model_table_appears_in_migrations() -> None:
    """模型里的每张表都要能在迁移里找到（Alembic 用单引号写表名）。"""
    text = _migration_text()

    missing = [
        name for name in Base.metadata.tables if f"'{name}'" not in text and f'"{name}"' not in text
    ]

    assert not missing, f"这些表没有出现在迁移里（改了模型没生成迁移？）：{missing}"


def test_models_are_all_registered() -> None:
    """模型汇总要真的把表注册进来（漏 import 会静默少表）。"""
    tables = set(Base.metadata.tables)

    assert len(tables) >= 20, f"注册的表太少（{len(tables)} 张），检查 models/__init__.py"
    for expected in ("users", "accounts", "courses", "task_points", "audit_logs"):
        assert expected in tables


def test_alembic_config_points_at_the_migration_directory() -> None:
    """``alembic.ini`` 的 script_location 必须指向仓库内的迁移目录。"""
    ini = (ROOT / "alembic.ini").read_text(encoding="utf-8")

    assert "src/chaoxing/storage/migrations" in ini


def test_alembic_heads_is_single() -> None:
    """只允许一个 head（多 head 说明迁移分叉了）。"""
    result = subprocess.run(
        ["uv", "run", "alembic", "heads"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )

    output = (result.stdout + result.stderr).strip()
    assert result.returncode == 0, output
    heads = [line for line in output.splitlines() if "(head)" in line]
    assert len(heads) == 1, f"存在多个迁移 head：\n{output}"
