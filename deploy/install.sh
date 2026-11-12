#!/usr/bin/env bash
# 一键部署（Debian/Ubuntu 与 RHEL 系适用）。
#
# 与旧版的区别：**不再系统级装 Python、不再 pip install** ——
# Python 与依赖都由 uv 管理（uv sync --frozen 严格按 uv.lock 安装）。
#
# 用法：
#   sudo bash deploy/install.sh
# 可用环境变量覆盖：APP_DIR APP_USER SERVICE PORT
set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
APP_USER="${APP_USER:-chaoxing}"
SERVICE="${SERVICE:-chaoxing}"
PORT="${PORT:-8000}"

log()  { printf '\033[1;34m[部署]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[注意]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[失败]\033[0m %s\n' "$*" >&2; exit 1; }

[[ "${EUID}" -eq 0 ]] || die "请用 root 执行：sudo bash $0"
[[ -f "${APP_DIR}/pyproject.toml" ]] || die "在 ${APP_DIR} 找不到 pyproject.toml，请确认代码已上传"
[[ -f "${APP_DIR}/uv.lock" ]] || die "找不到 uv.lock —— 依赖必须锁定，请完整上传仓库"

# ---------------------------------------------------------------------------
# 1. uv（Python 与依赖的管理者）
# ---------------------------------------------------------------------------
if ! command -v uv >/dev/null 2>&1 && [[ ! -x "${APP_USER_HOME:-/home/${APP_USER}}/.local/bin/uv" ]]; then
    log "安装 uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="${HOME}/.local/bin:${PATH}"
fi
command -v uv >/dev/null 2>&1 || die "uv 安装失败，请手动安装后重试"
log "uv：$(uv --version)"

id -u "${APP_USER}" >/dev/null 2>&1 || {
    log "创建运行用户 ${APP_USER}"
    useradd --system --create-home --shell /usr/sbin/nologin "${APP_USER}"
}

# ---------------------------------------------------------------------------
# 2. 依赖（严格按锁文件安装，不带任何开发依赖）
# ---------------------------------------------------------------------------
log "按 uv.lock 安装依赖"
cd "${APP_DIR}"
uv sync --frozen --no-dev
[[ -x "${APP_DIR}/.venv/bin/cx" ]] || die "依赖装好了但找不到 cx 入口，检查 pyproject 的 [project.scripts]"

# ---------------------------------------------------------------------------
# 3. 数据库与配置
# ---------------------------------------------------------------------------
log "执行数据库迁移"
uv run --no-sync cx db upgrade

# 密钥缺失时自动生成，并补齐缺失目录（可安全重复执行）
uv run --no-sync cx doctor --fix >/dev/null || warn "体检报告有需要人工处理的项，请稍后运行 cx doctor 查看"

chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}/data"
chmod 600 "${APP_DIR}/.env" 2>/dev/null || true

# ---------------------------------------------------------------------------
# 4. systemd 服务（ExecStart 指向 cx serve）
# ---------------------------------------------------------------------------
log "注册 systemd 服务 ${SERVICE}"
cat > "/etc/systemd/system/${SERVICE}.service" <<UNIT
[Unit]
Description=chaoxing-auto（学习通自动刷课）
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${APP_DIR}
# --workers 固定 1：进程内事件总线 + SQLite 单写者，多 worker 会同时破坏这两条前提
ExecStart=${APP_DIR}/.venv/bin/cx serve --host 127.0.0.1 --port ${PORT}
Restart=always
RestartSec=5
# 刷课是长任务，给它足够时间优雅退出（正在发的心跳不该被硬切）
TimeoutStopSec=30
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONUTF8=1
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable -q "${SERVICE}"
systemctl restart "${SERVICE}"

sleep 2
if systemctl is-active --quiet "${SERVICE}"; then
    log "服务已启动"
else
    journalctl -u "${SERVICE}" -n 30 --no-pager
    die "服务没起来，上面的日志是原因"
fi

# ---------------------------------------------------------------------------
# 5. 自检
# ---------------------------------------------------------------------------
log "自检 http://127.0.0.1:${PORT}/api/health"
if curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null; then
    log "健康检查通过"
else
    warn "健康检查没通过，看看：journalctl -u ${SERVICE} -f"
fi

log "部署完成。下一步："
echo "     1) Nginx 反代到 127.0.0.1:${PORT}（配置见 deploy/nginx.conf）"
echo "     2) 浏览器打开站点 → 注册**第一个**账号（它会自动成为管理员）"
echo "     3) 前端产物若缺失：在 web/ 下执行 pnpm build，本服务会自动开始托管"
echo "     4) 体检：uv run cx doctor --full（含前端托管与静态资源连通性）"
echo "     5) 日志：journalctl -u ${SERVICE} -f"
[[ -f "${APP_DIR}/web/dist/index.html" ]] || warn "还没构建前端产物：cd web && pnpm build"
