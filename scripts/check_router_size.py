"""API 路由规范检查（docs/REFACTOR_PLAN.md §5.11）。

三条机器判据，CI 里跑：

1. **一资源一包** —— ``api/routers/`` 下每个资源必须是包（目录 + ``__init__.py``），
   不能是 ``courses.py`` 这样的单文件；
2. **单文件 ≤ 200 行**；
3. **handler ≤ 15 行**（函数体有效行数：去掉 docstring、注释与空行）。

P4 之前 ``routers/`` 尚未存在，脚本直接通过并说明。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROUTERS = Path(__file__).resolve().parents[1] / "src" / "chaoxing" / "api" / "routers"

MAX_FILE_LINES = 200
MAX_HANDLER_LINES = 15
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def _is_route_handler(node: ast.AST) -> bool:
    """是否是被 ``@router.get/post/...`` 装饰的 handler。"""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute) and target.attr in HTTP_METHODS:
            return True
    return False


def _effective_body_lines(node: ast.FunctionDef | ast.AsyncFunctionDef, source: list[str]) -> int:
    """函数体的有效行数（排除 docstring、注释与空行）。"""
    body = node.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if not body:
        return 0

    first = body[0].lineno
    last = body[-1].end_lineno or body[-1].lineno
    count = 0
    for lineno in range(first, last + 1):
        text = source[lineno - 1].strip()
        if text and not text.startswith("#"):
            count += 1
    return count


def check_file(path: Path) -> list[str]:
    """检查单个文件，返回问题列表。

    :param path: ``.py`` 路径
    :returns: 人类可读的问题描述（空表示合规）
    """
    problems: list[str] = []
    relative = path.relative_to(ROUTERS.parent.parent.parent).as_posix()
    source = path.read_text(encoding="utf-8").splitlines()

    is_resource_file = path.parent == ROUTERS and path.name != "__init__.py"
    if is_resource_file:
        problems.append(
            f"{relative}: 资源必须是包（目录 + __init__.py），不能是单文件（§5.11 规则 1）"
        )

    if len(source) > MAX_FILE_LINES:
        problems.append(f"{relative}: {len(source)} 行 > {MAX_FILE_LINES}（§5.11 规则 2）")

    try:
        tree = ast.parse("\n".join(source))
    except SyntaxError as exc:  # pragma: no cover - 语法错误交给 ruff/tests 抓
        problems.append(f"{relative}: 语法错误 {exc}")
        return problems

    for node in ast.walk(tree):
        if _is_route_handler(node) and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lines = _effective_body_lines(node, source)
            if lines > MAX_HANDLER_LINES:
                problems.append(
                    f"{relative}: handler `{node.name}` 有效 {lines} 行 > "
                    f"{MAX_HANDLER_LINES}（§5.11 规则 3：业务逻辑属于 core.services）"
                )
    return problems


def main() -> int:
    """入口：返回进程退出码。"""
    if not ROUTERS.exists():
        print(f"[skip] {ROUTERS} 尚未创建（路由层 P4 落地）")
        return 0

    problems: list[str] = []
    files = sorted(p for p in ROUTERS.rglob("*.py") if "__pycache__" not in p.parts)
    for path in files:
        problems.extend(check_file(path))

    if problems:
        print("路由规范检查未通过：")
        for item in problems:
            print(f"  - {item}")
        return 1

    print(f"[ok] 路由规范检查通过（{len(files)} 个文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
