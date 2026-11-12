#!/usr/bin/env bash
# 从后端的 OpenAPI 生成前端类型（web/src/lib/api-types.ts 是生成物，要入库）。
#
# 为什么不 HTTP 拉 /api/openapi.json：直接调应用工厂 `create_app()` 拿 schema，
# 不需要起服务、不需要端口、不碰数据库 —— CI 里少一个会偶发失败的外部依赖。
# 两步都是幂等的：没改后端时重跑不会产生 diff（CI 就靠这个当"生成物是最新的"门禁）。
# 用法：bash scripts/gen_api_types.sh（从仓库根或 web/ 下跑都可以）
set -euo pipefail

# Windows 默认 GBK：JSON 里的中文描述会直接把 Python 写 stdout 打成 UnicodeEncodeError
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

cd "$(dirname "${BASH_SOURCE[0]}")/.."

OPENAPI=web/openapi.json
TYPES=web/src/lib/api-types.ts
# 临时名带 PID：多个 agent/终端同时跑这个脚本时不会互相截断同一个临时文件
TMP="$OPENAPI.$$.tmp"
trap 'rm -f "$TMP"' EXIT

command -v pnpm >/dev/null 2>&1 || {
    printf '\033[1;31m未找到 pnpm：生成前端类型需要它（corepack enable 或装 Node 后再来）\033[0m\n' >&2
    exit 1
}

# 先写临时文件再改名：生成失败时不会把上一次的 openapi.json 截成半截，留下一个坏文件
uv run --no-sync python -c "import json; from chaoxing.api.app import create_app; print(json.dumps(create_app().openapi(), ensure_ascii=False, sort_keys=True))" > "$TMP"
# 落盘前先验一遍能不能解析：两个终端同时跑（写过同一个文件）会得到半截 JSON，
# 这种事故要当场报错，而不是把一个坏 openapi.json 提交进仓库
uv run --no-sync python -c "import json,sys; json.load(open(sys.argv[1], encoding='utf-8'))" "$TMP"
mv -f "$TMP" "$OPENAPI"

( cd web && pnpm exec openapi-typescript openapi.json -o src/lib/api-types.ts )

printf '\033[1;32m已生成 %s 与 %s\033[0m\n' "$OPENAPI" "$TYPES"
