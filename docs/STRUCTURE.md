# chaoxing-auto 结构总览（重构参照）

> ⚠️ **历史文档 —— 它描述的是重构前的结构，不是当前代码。**
> 重构（P0~P7）已经完成，当前结构见 [`ARCHITECTURE.md`](ARCHITECTURE.md)，
> 旧代码的去向见 [`LEGACY_TRIAGE.md`](LEGACY_TRIAGE.md)。
> 保留本文档的理由：它是"为什么这么拆"的原始依据 ——
> 当时 110 个文件里每个的职责与代码量都摊在这里，对着它才能看出拆分是否遗漏了什么。

> 本文档是**重构起点**：把 legacy 分支上的真实结构一次性摊平，供后续拆分/重写时对照。
> 基线：独立仓库 `chaoxing-auto`，分支 `legacy` = `master` = `4f51bb7`（125 文件，工作区 clean）。
> 抽取方式：`ast.get_docstring` 全量扫描 110 个 Python 文件 + 路由装饰器实测，非推测。

---

## 1. 规模快照

| 分区 | 文件数 | 物理行数 | 说明 |
| --- | --- | --- | --- |
| `app/`（HTTP 适配层） | 10 | 1 998 | FastAPI 路由 + 依赖注入 + 模型 + 静态查询页 |
| `core/`（业务核心） | 38 | 10 692 | 协议 / 调度 / 存储 / AI / 工具 |
| `tools/`（运维与校准） | 61 | 9 659 | 离线自检、真实抓包、现场诊断 |
| `run.py` | 1 | 66 | 启动入口（uvicorn 封装） |
| **合计** | **110** | **≈22 415** | 另有 `deploy/` 4 文件、`docs/` 2 文件 |

最大的 6 个模块（重构时的重点拆分对象）：
`parser.py` 1025 · `worker.py` 974 · `repository.py` 894 · `video.py` 745 · `selftest.py` 728 · `quiz.py` 706

---

## 2. 目录结构与逐文件职责

### 2.1 `app/` — HTTP 适配层（1 998 行）

| 文件 | 行 | 职责 |
| --- | --- | --- |
| `__init__.py` | 8 | 包入口，导出 `create_app` |
| `main.py` | 146 | 应用装配 + lifespan（容器构造 → 起调度器/同步器 → 关停）；**安全闸门**：`0.0.0.0` 且无 `API_TOKEN` 时拒绝启动 |
| `deps.py` | 311 | 依赖注入容器 `Container`；`require_token`（机器凭证）/ `require_read_access` + `ViewerScope`（人凭证）/ `resolve_account_id` |
| `schemas.py` | 462 | 全部请求/响应模型（Pydantic v2），一个文件承载所有 API 契约 |
| `routers/__init__.py` | 8 | 路由集合（注意：**漏了 viewer**） |
| `routers/auth.py` | 228 | `/login`、`/login/import_cookie`、`/logout`、`/accounts*` |
| `routers/course.py` | 355 | `/course/scan`、`/select_course`、`/courses`、`/courses/available` |
| `routers/progress.py` | 156 | `/progress`、`/progress/finish` |
| `routers/ops.py` | 165 | `/health`、`/status`、`/tasks/run`、`/sync`、`/events`、`/endpoints`、`/points/reset` |
| `routers/viewer.py` | 159 | `/viewer/login`、`/viewer/me` —— 手机号换 HMAC 签名查看凭证，限流 60s/30 次防枚举 |
| `static/index.html` | — | 同源查询页（20.8 KB，10 s 轮询 `/progress`，不引外部 CDN） |

### 2.2 `core/chaoxing/` — 协议层（5 392 行，最大的一块）

| 文件 | 行 | 职责 |
| --- | --- | --- |
| `__init__.py` | 8 | 包入口 |
| `parser.py` | **1025** | 页面/接口解析：课程、章节、任务点卡片、题目、错误页识别；`ATTR_CANDIDATES` 字段候选表是校准的唯一开关 |
| `video.py` | **745** | 视频心跳：**只发心跳、不下载任何视频流**；2 倍速推进、抖动、回执判定 `isPassed` |
| `quiz.py` | 706 | 章节测验/随堂测验：抓题（换票 + 明文题干合并）、作答、提交、已批阅识别 |
| `endpoints.py` | 461 | 端点注册表：22 个端点，带校准状态 **VERIFIED 10 / TEMPLATE 11 / VOLATILE 1** |
| `browser_login.py` | 440 | Playwright 浏览器登录（自动填表 + 滑块拖动 + 人工接力）——可选增强依赖 |
| `session.py` | 345 | 账号级 HTTP 会话：连接池、限速（最小间隔 1.2s）、重试、登录态嗅探 |
| `login.py` | 325 | 账号密码登录四步流程（按线上实测实现，错误信息在 `msg2`） |
| `captcha.py` | 300 | 图形验证码（manual/ocr/platform 三模式）+ 滑块识别 |
| `course.py` | 247 | 课程列表、章节树、任务点发现 |
| `document.py` | 213 | 文档/PPT/阅读类任务点（`/ananas/job/document`、`/ananas/job/readv2`，必须解析 `status` 字段） |
| `models.py` | 182 | 跨层数据契约（Course/Chapter/TaskPoint/Question/Answer） |
| `exam.py` | 181 | 考试模块（默认只作答不交卷） |
| `session_store.py` | 130 | 登录态恢复：Cookie 优先、密码兜底 |
| `signature.py` | 84 | 视频心跳签名 |

### 2.3 `core/` 其余分区（5 300 行）

| 文件 | 行 | 职责 |
| --- | --- | --- |
| `ai/answer_engine.py` | 540 | 答案引擎：缓存 → 检索 → 批量问答 → 归一化 → 置信度 |
| `ai/websearch.py` | 299 | 自建联网检索（DDG / SearXNG / Bing），"外挂联网搜索" |
| `ai/deepseek.py` | 227 | DeepSeek 客户端（JSON 模式 + 并发闸门 + 429 退避） |
| `scheduler/worker.py` | **974** | 单账号刷课状态机：断点续播、播放单元编排（视频 + 紧随题目）、已批阅判定、`need_human` 处理 |
| `scheduler/report.py` | 414 | 刷课进度报表（`/progress/finish` 的数据源） |
| `scheduler/manager.py` | 206 | 账号并发管理器（信号量 + 后台循环 + 立即排入） |
| `scheduler/progress_sync.py` | 188 | 15 分钟进度快照 |
| `storage/repository.py` | **894** | 7 张表的 DAO，全部集中在一个模块 |
| `storage/db.py` | 323 | SQLite(WAL) 门面：异步包装、写锁、幂等建表 |
| `config.py` | 263 | 全局配置（pydantic-settings + `.env`），约 40 个配置项 |
| `exceptions.py` | 140 | 领域异常（含 `HumanInterventionRequired`、`EndpointUncalibrated`） |
| `constants.py` | 112 | 全局常量与枚举（域名、状态、题型） |
| `utils/crypto.py` | 209 | Fernet 加解密、密钥生成、**viewer token 的 HMAC 签发/校验** |
| `utils/text.py` | 102 | 题干归一化、指纹哈希、相似度、答案拆分 |
| `utils/logger.py` | 94 | 结构化日志（账号脱敏） |
| `utils/jitter.py` | 88 | 随机延迟 / 倍率抖动 / 限速器 / 指数退避 |
| `utils/retry.py` | 74 | 异步重试装饰器 |
| `utils/timeutil.py` | 56 | UTC 存储、本地展示 |

### 2.4 `tools/` — 61 脚本（9 659 行）

按"是否长期有用"分三类（依据各自 docstring 定位）：

**A. 长期装备（重构后应保留并 pytest 化）**
`selftest.py` 728（离线自检，39 项）· `live_test.py` 364（真账号端到端）· `smoke_api.py` 389（临时库起真实 HTTP 服务逐接口打通）· `batch_import.py` 382 · `check_deploy.py` 362（部署前"本地能跑服务器炸"检查）· `check_style.py` 313（把 CODING_STANDARD 变成可执行检查）· `test_qa.py` 259 · `assist_submit.py` 249 · `login_smoke.py` 230 · `reply_discussions.py` 228 · `preview_page.py` 224 · `probe_endpoints.py` 203（端点校准，"诚实性开关"）· `init_env.py` 195 · `setup_accounts.py` 161 · `cookie_from_browser.py` 128 · `test_font_decrypt.py` 122 · `test_document.py` 114 · `scan_points.py` 113 · `test_login.py` 60 · `test_db_lock.py` 60 · `check_snapshot.py` 58 · `check_activity.py` 51 · `check_pending.py` 36 · `list_courses.py` 40 · `disable_account.py` 25 · `toggle_account.py` 33 · `verify_config.py` 28 · `server_fix.py` 46 · `reset_*.py`（3 个，31–45 行）

**B. 现场校准（结论已沉淀进 parser/endpoints，可归档）**
`capture_heartbeat.py` 439 · `capture_submit.py` 359 · `capture_quiz.py` 302 · `probe_player.py` 223 · `probe_heartbeat.py` 190 · `probe_quiz_token.py` 184 · `probe_quiz_entry.py` 160 · `probe_quiz_api.py` 141 · `probe_dtoken.py` 104 · `diagnose_cards.py` 189 · `diagnose_courses.py` 210 · `diagnose_search.py` 107 · `verify_enc.py` 155 · `diff_live_vs_capture.py` 146 · `compare_heartbeat.py` 176 · `replay_capture.py` 117 · `dump_quiz_page.py` 135 · `analyze_quiz_page.py` 93 · `fetch_player_js.py` 159 · `fetch_reference.py` 125

**C. 一次性现场诊断（同批次排障产物）**
`diag_*.py` ×7（37–103）· `check_server_data.py` 30 · `check_deepseek.py` 113 · `clean_illustrations.py` 47

### 2.5 其它

| 路径 | 内容 |
| --- | --- |
| `run.py` | 66 行启动入口（argparse + uvicorn） |
| `deploy/` | `install.sh`（7 KB，Linux 部署）、`nginx.conf`、`openresty-site.conf`、`cert-renew-hook.sh` |
| `docs/CODING_STANDARD.md` | 162 行编码规范（模块头/类型注解/依赖方向），由 `tools/check_style.py` 机械校验 |
| `data/` | 运行时（**全忽略，仅留 `.gitkeep`**）：`chaoxing.db`、`logs/`、`captcha/`、`debug/`、`reference/` |
| `.env.example` | 128 行配置模板；`.env`/`.env.bak` 已被忽略 |
| `accounts.example.txt` | 账号清单模板（真实 `accounts.txt` 已忽略） |

---

## 3. 分层与依赖规则

```
app/                 HTTP 适配层（路由 / 模型 / 装配）
  ↓
core/scheduler/      编排层（账号并发 / 状态机 / 进度同步 / 报表）
  ↓
core/chaoxing/       协议层（HTTP + 解析，可脱离 FastAPI 独立运行）
  ↓
core/storage/  core/utils/    持久化与无状态工具
core/ai/  →  core/chaoxing/models  （单向，只依赖数据模型）
```

规范（`docs/CODING_STANDARD.md` 第 0 节）：**依赖只能单向向下**，禁止 `core/` import `app/`，禁止同层成环；唯一例外是 `app/deps.py`（装配点）。
`tools/` 下的脚本以 `core.*` 为公开 API 使用（`sys.path` 注入后 `# noqa: E402` 导入），这正是"协议层与调度层要能脱离 HTTP 单独跑"的验证。

---

## 4. 运行链路（关键数据流）

**启动**：`run.py` → `app.main:app` → lifespan：`Container.create()`（密钥 → 数据库 → AI 引擎 → 调度器 → 同步器）→ 启动后台调度循环 + 进度同步循环 → 对外服务；关闭时反序收尾。

**刷课**：`/login`（或 Cookie 导入）→ 课程列表 → `/course/scan` 落库任务点（`sequence = 章序号 × 1000 + 章内序号`）→ `/select_course` 勾选 → `scheduler.manager` 按 `MAX_ACCOUNT_CONCURRENCY` 派 worker → `worker` 按 `sequence` 升序取 pending，切成"播放单元"逐单元执行：
- `video` → `video.py` 发心跳（间隔 12–20 s，`rt=2.0±4%`，单步 ≤40 s，`isdrag=3`）→ 以服务端 `isPassed` 判完成
- `quiz`/`exam` → `quiz.py` 换票取题 → `answer_engine` 出答案 → 原样回传隐藏字段提交 → 回读校验
- `document`/`read` → `document.py` 调完成接口（校验响应体 `status`）
- 人脸/滑块 → `HumanInterventionRequired` → 标 `need_human`，等人工

**进度**：`progress_sync` 每 15 分钟把任务点聚合进 `progress_snapshots`；`/progress` 与网页查询页都只读快照（O(1)，不触网）。

---

## 5. HTTP 接口清单（实测装饰器，含鉴权）

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| POST | `/viewer/login` | 无（有同源限流） | 手机号换查看凭证 |
| GET | `/viewer/me` | 无 | 查看当前身份 |
| POST | `/login` | `require_token` | 登录，可选 `auto_start` |
| POST | `/login/import_cookie` | `require_token` | Cookie 导入（滑块/短信兜底） |
| POST | `/logout` | `require_token` | 清除登录态 |
| POST | `/accounts/{id}/enable` | `require_token` | 启用/暂停账号 |
| GET | `/accounts` | `require_token` | 账号列表 |
| POST | `/course/scan` | `require_token` | 扫描课程内容（`deep=false` 秒回缓存） |
| POST | `/select_course` | `require_token` | 勾选课程（单课程传 `course_key`） |
| GET | `/courses` | `require_read_access` | 课程缓存 + 视频/题统计 |
| GET | `/courses/available` | `require_read_access` | 可刷课程（剩余任务点/预计耗时） |
| GET | `/progress` | `require_read_access` | 进度快照 + 运行态 |
| GET | `/progress/finish` | `require_read_access` | 刷课结束报告 |
| GET | `/health` | `require_token` | 健康检查 |
| GET | `/status` | `require_token` | 调度器与账号总览 |
| POST | `/tasks/run` | `require_token` | 手动触发刷课（`wait=true` 同步等待） |
| POST | `/sync` | `require_token` | 手动触发进度同步 |
| GET | `/events` | `require_token` | 最近事件流 |
| GET | `/endpoints` | `require_token` | 端点校准清单 |
| POST | `/points/reset` | `require_token` | 重置任务点状态 |
| GET | `/accounts/{id}` | `require_token` | 单账号详情 |
| GET | `/` | — | 同源查询页（静态 HTML） |

> 路由层差异：`viewer` 无全局依赖、`auth/ops` 挂 `require_token`、`progress` 挂 `require_read_access`、`course` **逐路由**声明（同一文件里两种鉴权并存）。

---

## 6. 持久化（`data/chaoxing.db`，SQLite WAL，7 表）

| 表 | 作用 |
| --- | --- |
| `accounts` | 账号 + **加密**密码 + **加密**登录态 |
| `courses` | 课程与勾选状态 |
| `task_points` | 每个任务点的状态、已上报进度、播放顺序 `sequence` |
| `progress_snapshots` | 15 分钟粒度快照（`/progress` 数据源） |
| `tasks` | 持久化任务队列（进程被杀后自动放回） |
| `answer_cache` | 题库缓存（同题命中省一次模型调用） |
| `events` | 事件流 |

---

## 7. 配置分组（`.env` / `core/config.py`，约 40 项）

监听与鉴权（`HOST`/`PORT`/`API_TOKEN`/`ALLOW_OPEN_ACCESS`）· 请求节流（`REQUEST_MIN_INTERVAL`/`MAX_RETRY`/`PROXY`）· 视频心跳（`VIDEO_RATE`/`HEARTBEAT_INTERVAL_MIN·MAX`/`VIDEO_RATE_JITTER`/`MAX_PROGRESS_STEP`/`VIDEO_TASK_TIMEOUT`）· 验证码与人工介入（`CAPTCHA_MODE`/`CAPTCHA_PLATFORM_*`/`SLIDER_FALLBACK`/`ON_FACE_CAPTURE`）· 登录（`PASSWORD_ENCODING`/`LOGIN_FID`）· AI（`DEEPSEEK_*`/`ANSWER_*`/`MIN_ANSWER_CONFIDENCE`）· 检索（`WEBSEARCH_*`/`SEARXNG_BASE_URL`）· 提交策略（`AUTO_SUBMIT_QUIZ`/`AUTO_SUBMIT_EXAM`）· 快照（`PROGRESS_SYNC_INTERVAL`/`TASK_RETRY_DELAY`）· 并发（`MAX_ACCOUNT_CONCURRENCY`）。

三态鉴权模型：`127.0.0.1` + 空 token = 本机免鉴权；`0.0.0.0` + 有 token = 全接口需 `X-API-Token`；`0.0.0.0` + 空 token = **拒绝启动**（退出码 2）。

---

## 8. 重构视角：结构性问题清单

按"改动收益 / 风险"排序，均基于本次扫描事实：

1. **没有 `tests/`。** 全部验证压在 `tools/selftest.py`（728 行的自研断言）与一堆手工脚本上；README 自己承认缺"接口回归测试集"。重构第一步应先把它转成 pytest（`selftest` 里按模块组织的用例是现成素材）。
2. **三个千行级模块职责过载**：`parser.py`（1025，字段候选 + 多策略降级 + 错误页识别混在一起）、`worker.py`（974，状态机 + 业务分支 + 已批阅特判）、`repository.py`（894，7 表 DAO 单文件）。拆法是按"数据源/状态迁移"切，而非按行数切。
3. **`schemas.py` 462 行一锅端**：所有 API 契约单文件；`app/routers/__init__.py` 还漏了 `viewer`（架构漂移的活证据）。
4. **`tools/` 61 个脚本无索引**：B/C 两类（校准与现场诊断，约 30 个）结论早已沉淀进代码，却和长期装备并列。建议 `tools/archive/` 收编 + 一份 `tools/README.md` 说明"何时才需要跑"。
5. **端点校准状态是隐性契约**：22 个端点里 11 个 `template`，只有 `probe_endpoints.py` 能校准；重构时应把"未校准即失败"从运行时异常升级为测试期检查。
6. **无工程元数据**：没有 `pyproject.toml`（无 lint/format/打包配置），规范靠自研 `check_style.py`（ast + 正则）。
7. **文档漂移**：`README.md`（24 KB，内容质量高）未覆盖 `viewer.py` 全套准入机制、`browser_login.py`、`signature.py`、`report.py`；接口表缺 `/viewer/*`、`/sync`、`/points/reset`、`/progress/finish`、`/courses/available`、`/accounts/{id}`。
8. **行尾与平台陷阱已有护栏**：`.gitattributes` 锁 LF（否则 `deploy/*.sh` 上 Linux 会炸）；`.gitignore` 修正为 `data/*` 才能保住 `data/.gitkeep`。

---

## 9. 与父仓库的关系（重要）

- `chaoxing-auto` 现在是**独立仓库**，不再由任何父仓库跟踪。
- 若把它放进另一个仓库的子目录，执行 `git add -A` 时 git 会把它识别为 **embedded repository**，
  试图记录成 gitlink（submodule 样式）；需要在父仓库 `.gitignore` 里排除该目录。
- 若需要把旧历史带进独立仓库：父仓库可用 `git subtree split --prefix=chaoxing-auto -b cx-history`
  拆出该路径历史，再在新仓库 `git fetch <父仓库路径> cx-history` 合并。
