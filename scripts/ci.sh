#!/usr/bin/env bash
# 全部门禁一条命令跑完（scripts/ci.ps1 的 bash 等价版）。
# 用法：bash scripts/ci.sh
set -euo pipefail

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8   # Windows 默认 GBK：Python 子进程的中文输出会乱码/报错

cd "$(dirname "${BASH_SOURCE[0]}")/.."

step() { printf '\033[1;36m==> %s\033[0m\n' "$1"; shift; "$@"; }

step 'ruff check'    uv run ruff check .
step 'ruff format'   uv run ruff format --check .
step 'mypy'          uv run mypy
step 'import-linter' uv run lint-imports
step 'router shape'  uv run python scripts/check_router_size.py
step 'pytest'        uv run pytest

# 前端类型是生成物（web/openapi.json + web/src/lib/api-types.ts），入库但必须新鲜：
# 重新生成一遍，有 diff 说明改了后端 schema 却没重跑生成 —— 前端就会按旧字段编译通过、运行时 422。
step 'api types'     bash scripts/gen_api_types.sh
if ! git diff --exit-code -- web/openapi.json web/src/lib/api-types.ts; then
    printf '\033[1;31mweb/openapi.json 或 web/src/lib/api-types.ts 与后端 schema 不一致：跑 bash scripts/gen_api_types.sh 后提交\033[0m\n' >&2
    exit 1
fi

# 前端门禁：Biome 是前端唯一的 lint/format（定时器禁令也由它执行）。
# 没装 pnpm 的环境跳过，而不是让整条门禁挂掉 —— 后端仍是必过项。
if [ -f web/package.json ] && command -v pnpm >/dev/null 2>&1; then
    ( cd web && pnpm exec biome check src && pnpm run build )
else
    printf '\033[1;33m[skip] 未找到 pnpm，跳过前端门禁\033[0m\n'
fi

printf '\n\033[1;32m全部通过\033[0m\n'
