# 全部门禁一条命令跑完（docs/REFACTOR_PLAN.md §9 P0 的 DoD）。
# 用法：pwsh scripts/ci.ps1
# 任一环节失败即退出非零，可直接用于 CI 与本机自检。
$ErrorActionPreference = 'Stop'

# Windows 默认 GBK：Python 子进程（import-linter / 脚本的中文输出）会因此报错或乱码
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

function Invoke-Gate {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )
    Write-Host "==> $Label" -ForegroundColor Cyan
    # 原生命令往 stderr 写东西（uv 的进度、pnpm 的提示）不该被当成致命错误 ——
    # 在 $ErrorActionPreference='Stop' 下 PowerShell 5.1 会那样处理。成败一律看退出码。
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Action
    }
    finally {
        $ErrorActionPreference = $previous
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAIL: $Label（exit $LASTEXITCODE）" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

Invoke-Gate 'ruff check'        { uv run ruff check . }
Invoke-Gate 'ruff format'       { uv run ruff format --check . }
Invoke-Gate 'mypy'              { uv run mypy }
Invoke-Gate 'import-linter'     { uv run lint-imports }
Invoke-Gate 'router shape'      { uv run python scripts/check_router_size.py }
Invoke-Gate 'pytest'            { uv run pytest }

# 前端类型是生成物（web/openapi.json + web/src/lib/api-types.ts），入库但必须新鲜：
# 重新生成一遍，有 diff 说明改了后端 schema 却没重跑生成 —— 前端就会按旧字段编译通过、运行时 422。
# （与 scripts/ci.sh 同一道门禁，Windows 上跑 pwsh 时不该漏掉。）
if (Get-Command bash -ErrorAction SilentlyContinue) {
    Invoke-Gate 'api types（生成物新鲜度）' {
        bash scripts/gen_api_types.sh
        if ($LASTEXITCODE -ne 0) { return }
        git diff --exit-code -- web/openapi.json web/src/lib/api-types.ts
        if ($LASTEXITCODE -ne 0) {
            Write-Host "web/openapi.json 或 web/src/lib/api-types.ts 与后端 schema 不一致：跑 bash scripts/gen_api_types.sh 后提交" -ForegroundColor Red
        }
    }
}
else {
    Write-Host "[skip] 未找到 bash，跳过生成物新鲜度检查" -ForegroundColor Yellow
    $LASTEXITCODE = 0
}

# 前端门禁：Biome 是前端唯一的 lint/format（定时器禁令也由它执行）。
# 没装 node/pnpm 的环境跳过，而不是让整条 CI 挂掉 —— 后端仍是必过项。
if (Test-Path 'web\package.json') {
    if (Get-Command pnpm -ErrorAction SilentlyContinue) {
        Push-Location web
        try {
            Invoke-Gate 'biome（前端 lint/format）' { pnpm exec biome check src }
            Invoke-Gate 'tsc + vite build（前端产物）' { pnpm run build }
        }
        finally {
            Pop-Location
        }
    }
    else {
        Write-Host "[skip] 未找到 pnpm，跳过前端门禁" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "全部通过" -ForegroundColor Green
