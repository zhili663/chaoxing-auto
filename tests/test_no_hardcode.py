"""零硬编码扫描：把"禁止硬编码"变成机器判据（docs/REFACTOR_PLAN.md §5.10）。

不是覆盖率测试 —— 每一条规则都对应一类真实事故：

* 直读 ``os.environ``：配置来源会被打散到各处，运行时改配置失效（P4 的配置中心就白做了）；
* legacy 包名（``app`` / ``core`` / ``tools``）被重新 import：重构会退化成"两套代码共存"；
* 长数字字面量：课程 ID / 账号 ID 这类业务数据本该由网页 CRUD 决定，写进代码就会漂移。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "chaoxing"

#: 允许直读环境变量的白名单（目前为空：连 config.py 都走 pydantic-settings）
ENV_ACCESS_ALLOWLIST: frozenset[str] = frozenset()

_LEGACY_IMPORT_PATTERN = re.compile(r"^\s*(?:from|import)\s+(?:app|core|tools)\b", re.MULTILINE)
_LONG_NUMBER_PATTERN = re.compile(r"(?<![\w.])(\d{8,})(?![\w])")


class _EnvAccessFinder(ast.NodeVisitor):
    """在 AST 上找真正的环境变量访问（不误伤注释与文档字符串里的示例）。"""

    def __init__(self) -> None:
        self.hits: list[int] = []

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """``os.environ`` / ``os.getenv``。"""
        if (
            isinstance(node.value, ast.Name)
            and node.value.id == "os"
            and node.attr in {"environ", "getenv"}
        ):
            self.hits.append(node.lineno)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """``from os import environ``。"""
        if node.module == "os" and any(alias.name in {"environ", "getenv"} for alias in node.names):
            self.hits.append(node.lineno)
        self.generic_visit(node)


def _source_files() -> list[Path]:
    """返回 src/chaoxing 下所有 Python 源文件。"""
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _relative(path: Path) -> str:
    return path.relative_to(SRC.parent.parent).as_posix()


def _find_env_access(path: Path) -> list[int]:
    """返回该文件里直读环境变量的行号。"""
    finder = _EnvAccessFinder()
    finder.visit(ast.parse(path.read_text(encoding="utf-8")))
    return finder.hits


def test_no_direct_environment_access() -> None:
    """业务代码禁止直读环境变量，一律经 ``config.get_settings()``。"""
    offenders: list[str] = []
    for path in _source_files():
        if _relative(path) in ENV_ACCESS_ALLOWLIST:
            continue
        offenders.extend(f"{_relative(path)}:{lineno}" for lineno in _find_env_access(path))

    assert not offenders, "禁止直读环境变量（走 config.get_settings）：" + ", ".join(offenders)


def test_no_legacy_package_imports() -> None:
    """新代码不得 import legacy 的 app / core / tools 包。"""
    offenders: list[str] = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        if _LEGACY_IMPORT_PATTERN.search(text):
            offenders.append(_relative(path))

    assert not offenders, "不得 import legacy 包（app/core/tools）：" + ", ".join(offenders)


def _docstring_lines(tree: ast.AST) -> set[int]:
    """收集所有 docstring 覆盖的行号（文档里的示例不该被当成硬编码）。"""
    lines: set[int] = set()
    nodes = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, nodes) or not node.body:
            continue
        first = node.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            start = first.lineno
            end = first.end_lineno or start
            lines.update(range(start, end + 1))
    return lines


def test_no_hardcoded_business_ids() -> None:
    """8 位以上的数字字面量通常是硬编码的业务 ID（课程 / 账号）。

    这类值必须来自数据库或网页 CRUD，不能写进代码 —— 但**文档字符串里的示例不算**
    （扫描器用 AST 排除 docstring，避免"文档写个示例手机号就被判违规"的误报）。
    """
    offenders: list[str] = []
    for path in _source_files():
        source = path.read_text(encoding="utf-8")
        skip = _docstring_lines(ast.parse(source))
        for lineno, line in enumerate(source.splitlines(), start=1):
            if lineno in skip:
                continue
            code = line.split("#", 1)[0]
            if _LONG_NUMBER_PATTERN.search(code):
                offenders.append(f"{_relative(path)}:{lineno} → {code.strip()[:60]}")

    assert not offenders, "疑似硬编码业务 ID：" + "; ".join(offenders)


def test_source_tree_is_scannable() -> None:
    """扫描器自身必须真的扫到了文件（防"空跑通过"）。"""
    files = _source_files()

    assert files, f"没扫到任何源文件，检查路径：{SRC}"
    assert any(p.name == "config.py" for p in files)


#: 前端源码（同源守卫用：浏览器里只能有一个源）
WEB_SRC = Path(__file__).resolve().parents[1] / "web" / "src"


def test_frontend_api_calls_are_same_origin_relative() -> None:
    """前端不得出现绝对地址 —— 它是「浏览器里只有一个源」的前提。

    绝对地址会让 `pnpm dev` 的 Vite 代理失效，并在生产上变成跨域
    （Cookie 还得改成 SameSite=None）。注释里的外链不算。
    """
    files = list(WEB_SRC.rglob("*.ts*"))
    assert files, f"没扫到前端源文件，检查路径：{WEB_SRC}"

    offenders = [
        f"{path.name}:{number}"
        for path in files
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if not line.strip().startswith(("//", "*", "/*"))
        and ("http://" in line or "https://" in line)
    ]

    assert offenders == [], f"前端出现绝对地址（dev 代理与同源部署都会失效）：{offenders}"
