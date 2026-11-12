# 超星学习通自动任务脚本（chaoxing-auto）

把「看视频 / 读文档 / 做章节测验」这类重复任务点交给服务端跑：**不下载视频流**，只按真实时间节奏上报进度；
多账号并发由单进程 asyncio 管理；答案由可插拔的大模型引擎生成（OpenAI 兼容接口）。

> **本项目面向服务器部署**：一台 Linux 小机器（4C4G 足够）跑后端 + SQLite，Nginx 反代，浏览器访问网页控制台。
> 不建议在 Windows 桌面长期挂机，也不提供任何托管服务。

**讨论组（Telegram）**：<https://t.me/+QqG7bNMs5vMzYTg1>

---

## 功能

| 能力 | 说明 |
| --- | --- |
| 视频任务点 | 按心跳协议上报进度，不拉流；倍速固定 1.0（服务端按真实经过时间核对） |
| 文档任务点 | 走 `readjobv2` 完成阅读标记 |
| 章节测验 / 作业 | 换票 → 取题 → 生成答案 → 交卷；缺答案时**不交白卷** |
| 多账号 | 每账号独立会话、独立协程、粘性代理出口；单账号失败不影响其它账号 |
| 课程与任务点扫描 | 只扫「勾选要刷的课程」，顺序 + 限速 + 命中风控即停 |
| 断点续跑 | 任务点粒度检查点；支持跨进程暂停 / 继续 / 重置 |
| 代理池 | 可选接 `jhao104/proxy_pool` sidecar；403 自动冷却换出口 |
| 网页控制台 | 仪表盘 / 课程工作台 / 刷题 / 用户 / 设置；SSE 实时日志 |
| CLI | `cx` 命令覆盖账号、课程、刷课、统计、代理、数据库、诊断 |

## 已知缺陷与限制

请先读这一节，再决定是否部署。

1. **视频进度提交容易失败（最常见）**
   视频心跳偶发 403 或回执不置完成，表现为「任务点一直没刷完 / 进度卡住」。
   处理顺序：
   - 先 `cx study reset --account N` 把失败点打回队列，再重跑；
   - 仍失败则**重新设置心跳节奏**：网页「设置」→ 心跳区间，或 `.env` 里的
     `CHAOXING__HEARTBEAT_INTERVAL_MIN` / `CHAOXING__HEARTBEAT_INTERVAL_MAX`（建议 12–20 秒，别更激进）；
   - 用 `cx dev probe heartbeat --account N --point <任务点ID>` 检查该点是否具备心跳资格
     （从未真实播放过的视频需要先在浏览器里播几秒激活）。
2. **不要半夜刷，容易被 403 拒绝**
   平台风控在深夜 / 凌晨更敏感，同样的参数白天能过、半夜会被拒。建议在白天或晚间常规时段运行；
   同时把单账号并发控制在 3 以下、`CHAOXING__REQUEST_MIN_INTERVAL` 不低于 1.2 秒。
   触发 `antispiderShowVerify` 时应立即停服等待风控消退，不要继续重试。
3. **验证码需要人工介入**
   默认 `CHAOXING__CAPTCHA_MODE=manual`：网页会出现「需要人工」事件，用
   `cx auth login --account N --captcha <验证码>` 完成登录。
4. **答案引擎需要自备 API Key**
   未配置 `AI__API_KEY` 时只读本地题库缓存，未命中的题会跳过，测验点保持未完成（不会乱答、不会交白卷）。
   模型调用按你自己的服务商计费。
5. **平台协议会变**
   `mooc-ans/*` 的 403 规则与页面结构随平台更新变化；本项目按实测协议实现，不能保证长期免维护。
   排障记录见 [`docs/MOOC_ANS_403.md`](docs/MOOC_ANS_403.md)。

## 服务器部署（推荐）

前提：Debian / Ubuntu（或 RHEL 系）、root 权限、已装 `git` 与 `curl`。

```bash
# 1. 取代码
sudo git clone <你的仓库地址> /opt/chaoxing-auto
cd /opt/chaoxing-auto

# 2. 准备环境变量（至少确认 HOST、SECRET_KEY；AI Key 可选）
sudo cp .env.example .env
sudo vi .env        # APP__HOST=127.0.0.1；SECRET_KEY 留空会被 doctor 自动生成
                    # 答案引擎：AI__BASE_URL / AI__MODEL / AI__API_KEY

# 3. 一键部署：装 uv → 按 uv.lock 装依赖 → 迁移数据库 → 注册 systemd 并启动
sudo bash deploy/install.sh

# 4. 构建前端产物（服务器没 pnpm 时，也可在本地构建后上传 web/dist）
cd web && pnpm install && pnpm build && cd ..

# 5. Nginx 反代（把 deploy/nginx.conf 里的 server_name 改成你的域名）
sudo cp deploy/nginx.conf /etc/nginx/conf.d/chaoxing.conf
sudo nginx -t && sudo nginx -s reload

# 6. 体检
uv run cx doctor --full
```

浏览器打开站点 → **第一个注册的账号自动成为管理员**（之后建议关闭公开注册）。
HTTPS 用 certbot 申请，示例与续期钩子见 `deploy/`。

部署要点：

- systemd 直接跑 `cx serve`，`workers` 固定为 1（进程内事件总线 + SQLite 单写者）；
- 前端产物由 FastAPI 同源托管，`web/dist` 不存在时 API 仍可正常启动；
- `.env` 里的 `SECRET_KEY` 一旦更换，库里所有密文（账号密码、会话快照）**永久解不开**，迁移机器时一起带走；
- Nginx 的 `/api/events` 必须关闭缓冲，否则 SSE 日志不实时（`deploy/nginx.conf` 已配好）。

## 本地运行

```bash
uv sync
uv run cx db upgrade
uv run cx doctor --fix
uv run cx serve                  # http://127.0.0.1:8000
```

前端开发态：`cd web && pnpm install && pnpm dev`（Vite 代理 `/api` 到 8000）。

## 使用流程

网页控制台和 CLI 等价，下面是命令行最短路径：

```bash
# 添加学习通账号（手机号 / 学号；-p 留空则交互式输入密码）
uv run cx account add -u <账号> -p

# 登录（可能需要验证码）
uv run cx auth login --account 1
uv run cx auth login --account 1 --captcha 1234

# 扫描课程 → 勾选要刷的课 → 扫描任务点
uv run cx course scan --account 1
uv run cx course list --account 1
uv run cx course select --account 1 <课程键，如 265361351:151148348>
uv run cx course points --account 1 --course <课程键>

# 先小批量试跑，确认链路
uv run cx study run --account 1 --limit 3

# 正式跑（可暂停 / 继续 / 重置）
uv run cx study run --account 1
uv run cx study status --account 1
uv run cx study pause --account 1
uv run cx study resume --account 1
uv run cx study reset --account 1

# 查看进度
uv run cx stats --account 1
```

多账号：`uv run cx study run --all`，并发上限由 `CHAOXING__MAX_ACCOUNT_CONCURRENCY` 控制（默认 3）。

## 配置

三层优先级：**代码默认 → 环境变量（`.env`）→ 数据库覆盖**。

启动前确定：`APP__HOST`、`APP__PORT`、`APP__API_TOKEN`（非回环监听时必须设置）、`SECRET_KEY`、`AI__*`。

可热改：倍速（会被钳到 1.0）、心跳区间、并发数、模型名、代理池等，用网页设置页或：

```bash
uv run cx settings set chaoxing.video_rate 1.0
uv run cx settings show
```

完整键名见 [`.env.example`](.env.example)。

## 项目结构

```
chaoxing-auto/
├── src/chaoxing/
│   ├── client/     # 学习通协议：端点注册表 / 签名 / 模型 / 解析 / 分域 API
│   ├── core/       # 领域服务：账号 课程 计划 刷课 答案 统计 + 事件 + 调度
│   ├── storage/    # SQLAlchemy async + Alembic 迁移 + 仓储
│   ├── api/        # FastAPI：路由 / 鉴权 / 审计 / SSE / 静态托管
│   ├── cli/        # click 命令组 `cx`
│   └── obs/        # 事件总线与结构化日志
├── web/            # React 19 + Vite + TypeScript + shadcn/ui
├── tests/          # 分层测试
├── scripts/        # 门禁与验收脚本
├── deploy/         # install.sh / nginx.conf / OpenResty 与证书钩子
└── docs/           # 架构、协议、排障、自测清单
```

## 开发与验证

```bash
bash scripts/ci.sh     # ruff → mypy → import-linter → pytest → biome → tsc + vite build
uv run pytest -q       # 只跑后端测试
```

部署后建议按 [`docs/SELF_CHECK.md`](docs/SELF_CHECK.md) 走一遍真实链路自测（约 5 分钟）。

## 文档

- [架构](docs/ARCHITECTURE.md) · [结构](docs/STRUCTURE.md) · [协议覆盖](docs/PROTOCOL_COVERAGE.md)
- [403 排障记录](docs/MOOC_ANS_403.md) · [代理池接入](docs/PROXY_POOL.md)
- [部署后自测清单](docs/SELF_CHECK.md) · [验收记录](docs/ACCEPTANCE.md)
- [Android 协议抓包记录](docs/APP_PROTOCOL_2026-11-11.md)

## 安全与合规

- 仓库**不包含任何真实账号、密码、Cookie、API Key 或服务器域名**；`.env`、`accounts*.txt`、运行数据均已被 `.gitignore` 排除。
- 部署后请立即修改管理员默认口令；公网部署建议关闭公开注册，并设置 `APP__API_TOKEN`。
- 本项目仅用于**个人学习自动化**的技术研究。使用前请确认符合你所在学校 / 平台的服务条款与当地法律；
  因使用本项目导致的账号风控、封禁或其它后果，由使用者自行承担。

## 讨论

问题反馈、协议变化、部署经验：**<https://t.me/+QqG7bNMs5vMzYTg1>**
