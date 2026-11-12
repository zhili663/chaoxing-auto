# 部署 SOP

> 六条命令，从零到能用。前提：一台 Linux 服务器（Debian/Ubuntu 或 RHEL 系）、
> 有 root、装了 git 与 curl。

## 六条命令

```bash
# 1. 取代码（放在 /opt 下）
sudo git clone <仓库地址> /opt/chaoxing-auto && cd /opt/chaoxing-auto

# 2. 一键部署（装 uv → 按锁文件装依赖 → 迁移数据库 → 注册 systemd 并启动）
sudo bash deploy/install.sh

# 3. 构建前端产物（在开发机或服务器上均可；没有 pnpm 可先 npm i -g pnpm）
cd web && pnpm install && pnpm build && cd ..

# 4. 反代（把 deploy/nginx.conf 里的 server_name 改成你的域名后放到 nginx 配置目录）
sudo cp deploy/nginx.conf /etc/nginx/conf.d/chaoxing.conf && sudo nginx -s reload

# 5. 体检（含前端托管、静态资源、SSE 端点连通性）
uv run cx doctor --full

# 6. 浏览器打开站点 → 注册第一个账号（自动成为管理员）
```

## 这套部署的几个关键点

**运行方式**：systemd 直接跑 `cx serve`，`workers` 固定为 1 ——
进程内事件总线 + SQLite 单写者，多 worker 会同时破坏这两条前提（见 `docs/ARCHITECTURE.md` §9）。

**依赖**：`uv sync --frozen --no-dev` 严格按 `uv.lock` 安装，所以**部署结果可复现**；
服务器上不需要预装任何 Python —— uv 会自己管。

**前端**：产物由 API 同源托管（SPA fallback），因此没问题时**不需要**单独的静态站点；
`web/dist` 不存在时服务照常提供 API（`cx doctor --full` 会指出来）。

**配置**：`.env` 里放环境级配置（`SECRET_KEY` 会被 `cx doctor --fix` 自动补上）；
倍速、心跳区间、并发数、模型名这类**业务参数用网页或 `cx settings set` 改**，
它们写在数据库里，重启不丢。

**密钥**：`SECRET_KEY` 换了，旧密文（账号密码、会话快照）就**永久解不开**。
迁移机器时把它一起带走 —— 这一点在 `cx doctor` 里会看到提示。

## 升级

```bash
sudo git -C /opt/chaoxing-auto pull
cd /opt/chaoxing-auto
sudo uv sync --frozen --no-dev      # 依赖变更
uv run cx db upgrade                # 迁移
(cd web && pnpm install && pnpm build)
sudo systemctl restart chaoxing
uv run cx doctor --full
```

## 排障

| 现象 | 先看这里 |
| --- | --- |
| 服务起不来 | `journalctl -u chaoxing -n 50 --no-pager` |
| 页面 404 但接口正常 | 前端产物没构建（`web/dist/index.html` 不存在） |
| SSE 不推送 | Nginx 是否关了缓冲（见 `nginx.conf` 里的 `proxy_buffering off`） |
| 账号登录要验证码 | 网页右上角会有"需要人工"事件；用 `cx auth login --account N --captcha` |
| 数据库锁 | 确认只有一个进程在用这个库（`systemctl status chaoxing`） |

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `install.sh` | 一键部署（幂等，可重复执行） |
| `nginx.conf` | 反向代理（含 SSE 必需项） |
| `cert-renew-hook.sh` | 证书续期后重载 Nginx |
| `openresty-site.conf` | OpenResty 站点配置（用 OpenResty 时替代 nginx.conf） |
