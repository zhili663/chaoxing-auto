# 重构计划：chaoxing-auto v2

> 状态：**已执行**（rev 5，2026-10-11）。基线：`legacy` 分支 `4f51bb7`（125 文件 / 110 py / ≈22 415 行）。
> 本文件是**设计意图与决策依据**；实现现状以 `docs/ARCHITECTURE.md` 与 `docs/ACCEPTANCE.md` 为准，差异见文末 **§13 执行后偏离登记**。
> 蓝本：nonebot2 的架构范式（分层 / 依赖注入 / 生命周期 / 配置 / 权限组合子 / 事件钩子）——借范式，不借聊天机器人插件模型。
> 本文档中所有版本号均为 **2026-10-07 实测 PyPI 最新**，非记忆值。
> rev 4 变更：① **`pyproject.toml` 禁止手写** —— 骨架与依赖全部由 `uv init` / `uv add` 产出（§4.1 改为命令序列）；② **CLI 收紧**：只收后端该有的两级嵌套命令（`cx auth {login|logout|refresh|stats|import-cookie}` …），测试 / 校检 / 诊断 / 网页测试类子命令一律不进 CLI，网页与端到端检查收口到 `cx doctor --full`（§5.9）；③ `tools/` 61 项改为**五分类归置，禁止 1:1 搬家**（§8.2）。

---

## 0. 目标与"重构完成"的判据

**目标**：把 `legacy` 里三块互相纠缠的单体（千行 parser / 千行 worker / 千行 DAO）、裸 httpx 拼装、七张表的散装 SQL、以及一张 20.8 KB 的单文件网页，重写成**分层清晰、依赖注入、可测试、可观测**的现代 Python 服务 + 独立 SPA。

**完成判据（七条，全绿才算结束）**：
1. `uv sync --frozen && uv run cx doctor` 在干净机器上一次通过；
2. `uv run pytest` 覆盖 client（录制回放）/ storage（迁移）/ api（含权限与 SSE）/ core（状态机）四层，无网络用例可离线全绿；
3. `uv run ruff check` + `uv run mypy src` 零告警（core/client 层 `--strict`）；
4. 前端 `pnpm build` 产物由 API 托管，**Biome 的 `setTimeout/setInterval` 禁令生效且零违规**；
5. 四类可观测数据（app 日志 / 刷课记录 / 刷题记录 / 审计）分别可查，且经 SSE 实时可见；
6. 单账号真实刷课回归跑通（登录 → 扫描 → 视频心跳 → 测验作答 → 文档任务点 → 进度快照），与 legacy 行为逐项对齐，且 `docs/PROTOCOL_COVERAGE.md` 的刷课链路无"未建模"项；
7. 内存预算复核：3 账号并发常驻 < 260 MB（4C4G 目标不变）。

---

## 1. 现状病灶（重写依据，全部为实测事实）

| # | 问题 | 证据 |
| --- | --- | --- |
| 1 | **协议层单体化**：解析、降级策略、错误页识别、字段候选表全挤在一处 | `core/chaoxing/parser.py` 1025 行 |
| 2 | **无响应模型**：接口返回裸 `dict[str, Any]` 到处流，字段名靠 `.get()` 猜 | 全仓 grep：`def parse_*(...) -> dict` 遍布 parser/quiz/course |
| 3 | **调度层与业务逻辑粘连** | `core/scheduler/worker.py` 974 行，状态机 + 题型分支 + 特判混装 |
| 4 | **存储层手写 SQL + 单文件 DAO** | `core/storage/repository.py` 894 行；`db.py` 手写 WAL/锁 |
| 5 | **无测试目录**：验证靠 728 行自研断言脚本 | `tools/selftest.py`，仓库内无 `tests/` |
| 6 | **运维脚本 61 个平铺**，30 个是一次性现场产物 | `tools/` 实测分类见 `docs/STRUCTURE.md` §2.4 |
| 7 | **前端是单文件 HTML**，无构建、无类型、10 s 轮询 | `app/static/index.html` 20.8 KB |
| 8 | **鉴权只有静态 API_TOKEN**，无用户/角色概念，查询页靠手机号换 token | `app/deps.py`、`app/routers/viewer.py` |
| 9 | **无工程元数据**：没有 pyproject / ruff / mypy / alembic，行尾规范靠 `.gitattributes` 手补 | 仓库根实测 |
| 10 | **无依赖锁**：requirements.txt 手写 pin，无 lockfile、无虚拟环境管理器 | `requirements*.txt` |
| 11 | **业务决策无处安放**：刷哪些课、用什么模型、什么参数，现状只能改代码或 `.env` 再重启；网页只能粗放"勾选"，不能增删改查 | legacy 只有一个 `POST /select_course` 入口，无课程 / 计划 / 配置 CRUD |
| 12 | **路由与配置无规范**：`app/routers/` 5 个文件混装两种鉴权、`schemas.py` 462 行一锅端 | `docs/STRUCTURE.md` §2.1、§8 |

---

## 2. 目标架构：分层与依赖规则

```
┌──────────────────────────────────────────────────────────────┐
│  api/ (FastAPI)          cli/ (click)          web/ (SPA)    │  ← 入口层
├──────────────────────────────────────────────────────────────┤
│  core/   领域服务与编排：study / quiz / accounts / stats      │  ← 领域层
│          事件总线、调度器、领域模型（与 ORM 解耦）              │
├──────────────────────────────────────────────────────────────┤
│  client/ 学习通协议：transport / session / api / model        │  ← 外部适配层
│  ai/     LLM provider 抽象 + 检索 + 答案引擎                   │
├──────────────────────────────────────────────────────────────┤
│  storage/ (SQLAlchemy + Alembic)   obs/ (日志·事件·审计·指标)  │  ← 基础设施层
├──────────────────────────────────────────────────────────────┤
│  config.py        utils/                                       │  ← 全局
└──────────────────────────────────────────────────────────────┘
```

**硬规则**（由 `import-linter` 在 CI 强制，替代口头约定）：

1. `api` / `cli` 可以依赖 `core`，**`core` 永不可依赖 `api` / `cli`**；
2. `core` 只依赖 `client` / `ai` / `storage` / `obs` 的**抽象**（Protocol / 接口），不 import 其实现细节；
3. `client` **不依赖** `storage` / `core`（协议层可脱离一切单独跑，与 legacy 的 `tools/login_smoke.py` 同级能力）；
4. `obs` 是最底层，任何层都可以向它发事件；它不反向依赖任何层（只吃 `Event` 值对象）；
5. 同层内禁止成环。

**从 nonebot2 借的四件东西**：① 生命周期容器（`lifespan` 建/拆服务）；② 依赖注入（`svcs` registry + FastAPI `Depends` 语法糖）；③ 权限组合子（`SUPERUSER | require("study:start")`）；④ 领域事件钩子（`bus.subscribe(EventType, handler)`，对应 nonebot 的 hook/preprocessor 心智）。

---

## 3. 目标目录结构

```
chaoxing-auto/
├── pyproject.toml              # uv 项目定义（依赖 · 工具配置 · 入口脚本）
├── uv.lock                     # 锁定（入库）
├── .env.example  .gitignore  .gitattributes  .python-version
├── README.md
├── docs/
│   ├── STRUCTURE.md            # legacy 结构档案（只读留存）
│   ├── REFACTOR_PLAN.md        # 本文
│   ├── ARCHITECTURE.md         # 新架构说明（P7 产出）
│   └── CODING_STANDARD.md      # 继承并改写为 v2 规范
├── src/chaoxing/
│   ├── __main__.py             # python -m chaoxing → cli.main
│   ├── config.py               # 分域 Settings：App/Chaoxing/AI/Obs（嵌套 env）
│   ├── core/
│   │   ├── models.py           # 领域模型（pydantic，与 ORM 分离）
│   │   ├── events.py           # 领域事件 + EventBus（asyncio，进程内）
│   │   ├── scheduler.py        # 账号级并发调度（原 manager + progress_sync）
│   │   └── services/
│   │       ├── accounts.py     # 账号生命周期、登录态恢复、凭据加密
│   │       ├── groups.py       # ★ 虚拟账户组（批量管理 / 共享可见性的粒度）
│   │       ├── visibility.py   # ★ 数据可见性：超管见全部；其余人见自己 + 共享组
│   │       ├── courses.py      # ★ 课程 CRUD 与选择：网页决定"刷什么"的唯一入口
│   │       ├── plans.py        # ★ 刷课计划 CRUD（课程集合 + 策略），取代代码里的硬编码选择
│   │       ├── study.py        # 刷课编排（原 worker 状态机，按单元切分）
│   │       ├── quiz.py         # 答题编排（原 quiz + answer_engine 的编排侧）
│   │       ├── progress.py     # 进度快照与结束报告（原 progress_sync + report）
│   │       ├── settings.py     # ★ 配置中心：DB 覆盖层 · 热更新 · 元数据下发
│   │       └── stats.py        # KPI / 热力图 / 聚合查询
│   ├── client/
│   │   ├── transport.py        # 全局唯一 AsyncClient：连接池·限速·重试·代理
│   │   ├── session.py          # SessionState：cookie / referer / enc / dtoken
│   │   ├── registry.py         # ★ SessionRegistry：account_id → SessionState 的唯一真相源
│   │   ├── exceptions.py       # 协议异常（含 AuthExpired / HumanRequired）
│   │   ├── model/              # ★ pydantic 响应模型，一接口一模型
│   │   │   ├── base.py         #   ChaoxingModel（alias 容错 · 未知字段容忍 · 状态解包）
│   │   │   ├── login.py  user.py  course.py  chapter.py
│   │   │   └── video.py  quiz.py  exam.py  document.py  discussion.py
│   │   └── api/                # ★ 按 domain 的调用实现
│   │       ├── base.py         #   ApiModule：注入 session + transport
│   │       ├── login.py  user.py  course.py  chapter.py
│   │       └── video.py  quiz.py  exam.py  document.py  discussion.py
│   ├── ai/
│   │   ├── provider.py         # 薄 LLMProvider Protocol（唯一自制件，约 20 行）
│   │   ├── providers/
│   │   │   ├── openai_sdk.py   #   openai SDK + instructor 适配（首选，见 §4.3）
│   │   │   └── fake.py         #   测试用假 provider（离线可跑）
│   │   ├── search.py           # 检索（DDG/SearXNG/Bing）
│   │   ├── prompts.py          # 提示词集中管理（可测）
│   │   └── engine.py           # 答案引擎：缓存→检索→结构化输出→归一化
│   ├── storage/
│   │   ├── base.py             # DeclarativeBase + 命名约定 + 通用 mixin
│   │   ├── models/             # ORM：user.py rbac.py account.py course.py plan.py task.py quiz.py settings.py audit.py stats.py
│   │   ├── repositories/       # 按聚合根拆分（不再单文件 894 行）
│   │   ├── uow.py              # UnitOfWork：async session 生命周期与事务边界
│   │   ├── engine.py           # async engine（aiosqlite + WAL pragma）
│   │   └── migrations/         # Alembic（env.py + versions/）
│   ├── obs/
│   │   ├── logging.py          # structlog：JSON 文件 + 控制台 + 请求上下文绑定
│   │   ├── bus_sink.py         # 事件 → SSE 广播 + 落库
│   │   ├── audit.py            # 行为审计写入（谁·何时·对什么·从哪来）
│   │   └── metrics.py          # 计数/时序聚合（进程内，无外部依赖）
│   ├── api/
│   │   ├── app.py              # create_app() + lifespan（建容器 → 起调度 → 关停）
│   │   ├── container.py        # svcs registry 装配（P0 起即可被 CLI 复用）
│   │   ├── deps.py             # 依赖：Settings/Container/UoW/current_user/require(...)
│   │   ├── security.py         # 口令 proof 校验 · argon2 · 会话 cookie · CSRF
│   │   ├── errors.py           # 统一异常 → RFC 7807 problem+json
│   │   ├── middleware.py       # request-id · 审计 · 访问日志 · CORS
│   │   ├── sse.py              # SSE 端点（事件流 / 日志流）+ keep-alive
│   │   ├── schemas/            # Pydantic DTO（按域拆，告别 462 行单文件）
│   │   └── routers/            # ★ 一资源一包，包内按动作再拆文件（规范见 §5.11）
│   │       ├── auth/           #   login.py · register.py · session.py
│   │       ├── users/          #   crud.py · password.py · sessions.py
│   │       ├── roles/          #   crud.py · permissions.py · binding.py
│   │       ├── accounts/       #   crud.py · lifecycle.py · probe.py
│   │       ├── courses/        #   crud.py · scan.py · selection.py · detail.py
│   │       ├── study/          #   plans.py · control.py · records.py · progress.py
│   │       ├── quiz/           #   attempts.py · cache.py · manual.py
│   │       ├── settings/       #   schema.py · values.py · prompts.py
│   │       ├── stats/          #   overview.py · heatmap.py · users.py
│   │       ├── logs/ stream/ system/
│   ├── cli/
│   │   ├── main.py             # click group `cx`（从 container 取依赖，与 API 共用）
│   │   ├── serve.py user.py account.py group.py course.py study.py db.py probe.py capture.py doctor.py
│   └── utils/
│       ├── crypto.py  text.py  jitter.py  time.py  retry.py  pagination.py
├── web/                        # 前端（完全重写，见 §6）
├── tests/
│   ├── conftest.py
│   ├── fixtures/               # 学习通真实响应录制（脱敏）
│   ├── client/ api/ core/ storage/
└── deploy/                     # systemd unit · nginx · 证书钩子（uv 版）
```

---

## 4. 技术选型与依赖（含两处事实纠正）

### 4.1 依赖与骨架：全部由 `uv` 生成（**禁止手写 pyproject**）

```bash
uv init --package chaoxing-auto --python 3.12        # 生成骨架（含 [project.scripts] 占位）
uv add fastapi "uvicorn[standard]" pydantic pydantic-settings \
       "sqlalchemy[asyncio,aiosqlite]" alembic aiosqlite httpx click rich \
       svcs structlog argon2-cffi cryptography beautifulsoup4 lxml fonttools \
       sse-starlette orjson websockets openai instructor
uv add --optional browser playwright                 # 滑块账号才需要
uv add --optional ocr ddddocr                        # 缺省走人工识别
uv add --dev pytest pytest-asyncio respx ruff mypy import-linter polyfactory
uv lock                                              # 产出 uv.lock（入库）
```

规则：
- **依赖清单一律用 `uv add` / `uv remove` 维护，禁止手工编辑 `pyproject.toml` 的 dependencies**；版本解析交给 `uv.lock`，不手写 pin；
- 全文**唯一允许人工维护**的是 `[project.scripts]` 的入口指向（`cx = "chaoxing.cli.main:cli"`）与 `[tool.ruff] / [tool.mypy] / [tool.pytest.ini_options]` 配置段 —— 这是 uv 的能力边界（它没有对应命令），不是偷懒；
- 运行期一律 `uv run`：`uv run cx …`、`uv run pytest`、`uv run uvicorn …`；CI 用 `uv sync --frozen`；
- `cx doctor` 检查 `pyproject.toml` 的依赖段是否与 `uv.lock` 漂移（有人手改过就会报错）。

### 4.2 纠正：`sqlalchemy[sqlite3]` 不是有效 extra

实测 `sqlalchemy==2.1.3`（PyPI `info.provides_extra`）提供的 extras 是：`asyncio, mypy, mssql*, mysql*, oracle*, postgresql*, aiomysql, aioodbc, asyncmy, aiosqlite, sqlcipher`。**没有 `sqlite3`**——SQLite 驱动是标准库 `sqlite3`，SQLAlchemy 内置支持，不通过 extra 分发；写了它只会得到 pip/uv 的 "unknown extra" 警告。异步用法要的是 `sqlalchemy[asyncio]`（拉 greenlet）**加上** `aiosqlite`（真正的 async DBAPI），即 `sqlalchemy[asyncio,aiosqlite]`。

### 4.3 LLM 客户端：用成熟库，不自造轮子

DeepSeek 开放平台提供 **OpenAI 兼容端点**，所以这里不需要自写任何 HTTP / 重试 / 流式解析层。

| 候选 | 结论 |
| --- | --- |
| Vercel AI SDK 的 Python 版（PyPI 实测 `vercel-ai-sdk 0.0.1.dev10`） | ❌ **弃用**：实验版本，生态未成型 |
| **`openai`（OpenAI 官方 SDK）** | ✅ **首选**：成熟、高星、DeepSeek 官方文档即用 `base_url` 指向自家端点；重试/超时/流式/并发全在库里 |
| `instructor` | ✅ **采用**：在 openai SDK 之上做 pydantic 结构化输出（`response_model=`），省掉手写 JSON 容错 |
| `litellm` | 🅱 备选：将来要真正多 provider 时再换，调用面同构 |

保留一个**薄契约**（约 20 行，仅为测试替换与将来换库，不是自制 HTTP 客户端）：

```python
# src/chaoxing/ai/provider.py —— 唯一自制的东西
class LLMProvider(Protocol):
    async def generate_object[T: BaseModel](
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        schema: type[T],
        temperature: float = 0.0,
    ) -> T: ...
    def stream_text(self, *, model: str, messages: Sequence[ChatMessage]) -> AsyncIterator[str]: ...
```

依赖由 CLI 解析而非手写 pin：`uv add openai instructor`，版本进 `uv.lock`。

### 4.4 其余选型说明

| 选择 | 理由 |
| --- | --- |
| **uv**（尚未安装，P0 第一步） | 依赖 + 虚拟环境 + Python 版本 + lockfile 一把抓；前端用 pnpm |
| **svcs**（服务定位器, v26.2.0） | Starlette 原生、随 lifespan 建拆、**CLI 可复用同一容器**；比手工单例好测，比 dependency-injector 少样板 |
| **SQLAlchemy 2.0 async + Alembic** | 告别手写 SQL 与"幂等建表"；schema 演进有版本 |
| **structlog** | 结构化日志 + contextvars 绑定 request/account 上下文，天然支持"四通道"分流 |
| **argon2-cffi**（弃 passlib） | passlib 维护停滞；argon2-cffi 是上游实现，直接可用 |
| **sse-starlette** | 处理 keep-alive ping、断连清理，比手写 StreamingResponse 少踩坑 |
| **ruff + mypy + import-linter** | Python 侧 linter / 类型 / 依赖规则的机器判据，替代自研 `check_style.py` |
| **Biome**（替代 ESLint + Prettier） | 前端唯一的 lint + format 工具，Rust 单二进制、零插件生态、配置一份 `biome.json` |
| **respx + 录制 fixture** | 学习通不可 mock 云端，只能**先录后放**：真实响应落盘 → 参数化回归 |

### 4.5 脚手架优先（能生成的一律不手写）

**规范**：凡官方 CLI 能生成/维护的东西，禁止手工创建。`cx doctor` 会检查以下命令的产物是否与生成器保持一致。

| 场景 | 命令（P0/P5 直接执行） |
| --- | --- |
| Python 项目骨架与依赖 | `uv init --package` · `uv add/remove` · `uv lock` · `uv sync --frozen` · `uv run <cmd>` —— **pyproject 依赖段禁止手写**（§4.1） |
| Python lint/format | `uvx ruff check --fix`、`uvx ruff format`、`uvx mypy src` |
| 数据库迁移 | `uvx alembic init src/chaoxing/storage/migrations`、`uvx alembic revision --autogenerate` |
| 前端骨架 | `pnpm create vite web --template react-ts` |
| UI 组件 | `pnpm dlx shadcn@latest init` + `pnpm dlx shadcn@latest add button table tabs sheet card switch select …` |
| 前端 lint/format | `pnpm dlx @biomejs/biome check --write .`、`biome init` |
| 前端类型（由后端契约生成） | `pnpm dlx openapi-typescript http://127.0.0.1:8000/openapi.json -o src/lib/api-schema.d.ts` |
| 前端包管理 | `pnpm add ky swr react-router @tanstack/react-table recharts` |

---

## 5. 关键设计（Python 侧）

### 5.1 配置：分域 + 嵌套 env + 单一来源

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="__", env_file=".env", extra="ignore")
    app: AppSettings  # host/port/data_dir/log_level
    chaoxing: ChaoxingSettings  # 倍速·心跳抖动·限速·验证码模式·代理
    ai: AISettings  # provider/model/key/并发/检索
    obs: ObsSettings  # 日志级别·SSE 缓冲·审计开关
```
环境变量形如 `CHAOXING__VIDEO_RATE=2.0`、`AI__PROVIDER=openai`。**旧 `.env` 提供迁移映射表**（原 `tools/init_env.py` 的逻辑并入 `cx doctor --fix`）。

### 5.2 依赖注入：一个容器，三处复用

```
container.py  build_container(settings) -> svcs.Registry
   ├─ api/app.py      lifespan 挂载/卸载          （HTTP 入口）
   ├─ cli/main.py     命令内 ctx.obj 取同一容器    （CLI 入口）
   └─ tests/conftest  registry 覆盖任意依赖        （测试入口）
```
HTTP 层再包一层语法糖：`SettingsDep / UoWDep / CurrentUser / require("study:start")`。**测试通过 registry 替换**（例如把 LLM provider 换成 Fake、把 storage 换成内存库），不需要 monkeypatch。

### 5.3 事件总线：解耦刷课与"看刷课的人"

```python
@dataclass(slots=True, frozen=True)
class TaskPointFinished(Event):
    account_id: int
    course_key: str
    point_id: str
    kind: PointKind
    status: TaskStatus
    elapsed_s: float


bus.subscribe(TaskPointFinished, sse_broadcaster)  # → SSE
bus.subscribe(TaskPointFinished, metrics_aggregator)  # → activity_daily（热力图）
bus.subscribe(TaskPointFinished, study_recorder)  # → task_events（刷课记录）
```
领域服务**只发事件，不知道谁在听**——这是"高内聚低耦合"的落点，也是 SSE、统计、审计三条线共用的唯一钩子。实现：进程内 `asyncio.Queue` + `TaskGroup` 分发，慢订阅者不阻塞发布者（有界队列 + 丢弃策略 + 计数）。

### 5.4 client 层：多会话共享一条 AsyncClient（含陷阱处理）

**需求**：N 个账号（N 个 cookie 域）共享一个连接池，而不是 N 个 AsyncClient。

```python
class ChaoxingTransport:
    """全局唯一 httpx.AsyncClient：连接池/超时/代理/重试/限速都在这里。"""

    def __init__(self, settings) -> None:
        self._client = httpx.AsyncClient(
            timeout=...,
            proxy=...,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
            headers=BASE_HEADERS,
            # ★ 刻意不设 cookies：client 级 cookie jar 会让多账号互相串号
        )

    async def request(self, session: SessionState, spec: RequestSpec) -> Response:
        """session 决定 cookie/referer/UA；transport 只负责发。"""
```

三个必须显式处理的点（legacy 踩过、v2 写进契约）：
1. **cookie 不走 client jar**：每请求 `cookies=session.cookies`；响应 `Set-Cookie` 由 `session.absorb(response)` 手动吸收，**含 `response.history` 里 302 链上的每一个 Set-Cookie**（`follow_redirects=True` 时最容易漏）；
2. **限速是会话级的**：`REQUEST_MIN_INTERVAL` 作用在 per-account 令牌桶（`utils/jitter.py` 的 Limiter），不是全局，否则多账号并发被自我节流；
3. **重试是幂等的**：只对 GET/心跳类幂等请求自动重试，`POST`（提交测验/交卷）永不自动重试，避免"重复交卷"事故。

**api 层按 domain 切**，每个 `ApiModule` 只懂自己那几个端点：

```python
class VideoApi(ApiModule):
    async def heartbeat(self, s: SessionState, req: HeartbeatRequest) -> HeartbeatReply:
        raw = await self.transport.request(s, spec("heartbeat", json=req.model_dump(by_alias=True)))
        return HeartbeatReply.model_validate(raw.json())  # ★ 一接口一模型
```

**model 层策略**（针对学习通最恶心的地方）：
- 基类 `ChaoxingModel`：`populate_by_name` + `alias_generator`，未知字段**容忍不报错**（`extra="ignore"`），数字/字符串混用统一 coerce；
- **HTTP 200 ≠ 成功**：`ChaoxingEnvelope[T]` 统一解包 `status/msg/msg2`，`status=false` 抛领域异常（legacy 的 200 错误页教训，测试里固化）；
- **字体反爬**保留 `fonttools` 子模块（`client/model/font.py`），输入是字体二进制、输出是码点→字符映射；
- **错误页指纹**（3004 / 温馨提示 / 权限 / enc校验失败）集中一处 `ErrorPageDetector`，任何解析入口先过它。

### 5.5 storage 层：SQLAlchemy 2.0 async + UoW + Alembic

- 引擎：`create_async_engine("sqlite+aiosqlite:///data/app.db")`，连接事件里 `PRAGMA journal_mode=WAL, synchronous=NORMAL, busy_timeout=5000`；
- **单写者约束显式化**：`UnitOfWork` 串行化写事务（进程内 `asyncio.Lock`），读路径不锁；
- 仓储按聚合根拆：`UserRepo / AccountRepo / CourseRepo / TaskPointRepo / QuizRepo / AuditRepo / StatsRepo`；
- 迁移：Alembic 从零建表；旧库（7 表）走一次性 `cx db import-legacy`（见 §8）；
- ORM 与领域模型**分离**：ORM 不出 `storage` 边界，`core` 只见 pydantic 领域模型（避免 legacy 那种"处处 Row/dict"）。

### 5.6 可观测性：四通道，物理隔离

| 通道 | 载体 | 内容 | 查询入口 |
| --- | --- | --- | --- |
| **app 日志** | structlog → `data/logs/app.log`(JSON) + 控制台 | 进程/请求/依赖健康度，带 `request_id`、`account_id` 绑定 | `GET /api/v1/logs?stream=app` + SSE `/stream/logs` |
| **刷课记录** | 表 `study_sessions` / `task_events` | 每个任务点的开始/心跳数/完成/失败原因/耗时 | 课程工作台 + `GET /api/v1/study/records` |
| **刷题记录** | 表 `quiz_attempts` + `answer_cache` | 题目指纹、答案来源（缓存/模型/检索）、置信度、提交回执 | 刷题页 + `GET /api/v1/quiz/attempts` |
| **行为审计** | 表 `audit_logs` | 谁（user）·何时·对哪个资源·做了什么·来源 IP/UA·结果 | 用户管理页 + `GET /api/v1/system/audit` |

四条线共用 §5.3 的事件总线，但**落点、保留策略、可见性各自独立**（审计仅管理员可见，日志可裁剪级别）。

### 5.7 API 层：FastAPI 最佳实践落点

- 路由前缀 `/api/v1`，按域分文件，`include_router(..., dependencies=[...])` 声明式挂鉴权；
- **契约**：`schemas/` 按域拆；响应统一 `Envelope[T]`（`{code, message, data, request_id}`）；错误走 `problem+json`；
- **依赖**：`current_user`（会话 cookie → 用户）、`require("perm:node")`（权限组合子）、`UoWDep`（事务边界在依赖里进出）；
- **中间件**：request-id 注入 → 审计（对写操作）→ 访问日志 → CORS（仅开发）；
- **SSE**：`/api/v1/stream/events`（领域事件）、`/api/v1/stream/logs`（日志，支持 `?min_level=`）；`sse-starlette` 负责 ping 与断连清理；**单进程约束**（多 worker 需引入外部广播，见 §10 风险 6）；
- **OpenAPI** 自动产出，前端 `openapi-typescript` 生成类型（**契约即类型**，杜绝手写 DTO 漂移）。

### 5.8 鉴权与权限（用户系统 v1）

**权限模型**：`users` ← `user_roles` → `roles` ← `role_permissions` → `permissions`（权限节点表）。
**首个用户**：首次注册时若 `users` 表为空 → 自动授予 `superuser`（持通配 `*`），创建动作进审计。

**权限节点的两重语义**（rev 3 关键修正）：
1. **操作权**：能不能做这件事（`course:write`）；
2. **数据范围**：能看 / 能操作**谁的数据**（`account:read:own` vs `account:read:all`）。

**数据可见性规则** —— 统一收口在 `core/services/visibility.py`，**所有查询在仓储层强制注入 `visible_account_ids()`**，绝不靠前端筛选：

| 身份 | 可见范围 |
| --- | --- |
| `superuser` / 持 `account:read:all` | **全部账号及其绑定内容**（课程、任务点、刷课记录、刷题记录、统计）—— 即"管理员可查看所有账号绑定的内容" |
| 普通用户 | `owner_user_id = 我` 的账号 **∪** 我所在虚拟账户组内共享的账号 |
| 无可见账号 | 返回**空集**（前端显示"暂无账号"），而不是 403 |

**SSE 与 CLI 同样受约束**：`/stream/*` 在建立订阅时就按订阅者可见集过滤（事件 payload 只推可见账号的事件，避免日志/进度泄漏他人数据）；`cx` 命令行同样经过 `visibility.py` —— 数据范围是**仓储层的属性**，不是 API 层的装饰。

**账号绑定模型（沿袭现有思路 + 解耦）**：
- **不强制**"平台用户 ↔ 超星账号"一一绑定：`accounts.owner_user_id` **可空**，管理员批量导入的超星账号可以不属于任何平台用户；
- 需要归属时**随时绑定/解绑**（`PATCH /accounts/{id}` 设 `owner_user_id`），不做强制迁移、不做初始化强制分配；
- **虚拟账户组**（`account_groups` + `account_group_members`）：把 N 个超星账号装进一个逻辑容器（"机房 A 的 30 个号"、"某班 50 人"），**组是批量操作与共享的粒度** —— 批量启停、批量建计划、整组可见性；组本身可空属主（系统组）；
- 基数：一条超星账号 = 0..1 个属主 + 0..N 个组；一个平台用户 = 0..N 条账号。三者解耦，为将来真实的实名绑定留位。

**口令链路**（前端 SHA-256 + 后端 Argon2id，附安全边界说明）：
```
注册:  C = sha256(password)            ← 前端 WebCrypto，明文不出浏览器
       POST /auth/register {username, C}
       后端存 Argon2id(C)              ← 加盐、慢哈希，绝不明文也绝不存 C
登录:  GET  /auth/challenge → {nonce, ttl=60s, 一次性}
       proof = sha256(sha256(password) + ":" + nonce)   ← 防报文重放
       POST /auth/login {username, proof, nonce}
       后端用 C 校验 proof…… ✗
```
⚠️ **这里有一个必须讲清的技术事实**：`proof` 一旦掺入 nonce，服务端存的是 `Argon2id(C)`，**无法从 proof 反推 C**，也就无法用存储哈希校验。两条可行路线：
- **路线 A（推荐，简单可靠）**：登录时直接传 `C`（在 TLS 内），后端 `Argon2id.verify(C, stored)`；nonce 机制仅用于**注册**与**口令修改**这类低频高危动作，并配合严格限速。C 在传输层上等价于口令，安全性依赖 **TLS + HttpOnly Cookie + 限速**，而不是 nonce。
- **路线 B（PAKE，如 SRP）**：真正的"零明文/零等价物"协议，但实现复杂、学习成本高，**本期不采用**。

会话：`sessions` 表存不透明 token（可撤销、可审计、`ip`/`ua`/`last_seen`），HttpOnly + SameSite=Lax Cookie；写操作校验 `Origin` 头（CSRF 防线）。**SSE 用同一 Cookie**，因此 `EventSource` 可直接使用（无需自定义头）。

**权限节点树**（含数据范围节点）：
```
account:read | account:read:all | account:write | account:delete | account:login
group:read | group:write
course:read | course:scan | course:write | course:delete | course:select
study:plan:read | study:plan:write | study:plan:run | study:plan:control | study:record:read
quiz:read | quiz:write | quiz:submit | quiz:cache:write
user:read | user:write | role:read | role:manage
settings:read | settings:write | prompt:write
system:audit | system:logs | system:task | system:doctor
stream:subscribe
```
前端据此渲染菜单/按钮与数据范围切换（`usePerm("account:read:all")`）；后端**每个路由都要挂 `require(...)`，读路由同样要挂**（数据范围由它决定）。`tests/api/test_permissions.py` 遍历路由表做"未挂鉴权即失败"的元测试。

### 5.9 CLI：click 命令组 `cx`（与 API 共用容器）

**设计约束（rev 4 收紧）**：CLI 只承载**后端本该有的运维与开发者命令**，允许 `cx <域> <动作>` 两级嵌套；**严禁把 `tools/` 里的脚本 1:1 搬成子命令**。

```bash
cx serve                                # 起 API（生产挂 web/dist）
cx doctor [--full] [--raw] [--fix]      # 唯一体检入口（语义见下）
cx auth    {login|logout|refresh|stats|import-cookie}   # 超星账号：认证与登录态
cx account {list|add|import|enable|disable|remove}      # 超星账号实体（属主可空）
cx group   {list|create|add|remove}                     # 虚拟账户组
cx course  {list|scan|select}                           # 课程（网页 CRUD 的命令行对应物）
cx study   {run|status|pause|reset|logs}                # 刷课控制
cx user    {list|create|grant|revoke|passwd}            # 平台用户与角色
cx db      {upgrade|import-legacy|stats|shell}          # 数据库与迁移
cx dev capture {proxy|browser|diff}                     # 开发者子命令：抓包工具链（§5.14）
cx dev probe   {endpoints|heartbeat|quiz|font|ai}       # 开发者子命令：协议校准与 AI 连通性
```

**`cx doctor` 语义**（替代所有"检查类脚本"）：

| 模式 | 内容 |
| --- | --- |
| 默认 | 环境与依赖（uv lock 漂移、Python 版本）→ 配置（必需项、冲突项）→ 数据库（schema 版本、WAL、锁） |
| `--full` | 追加端到端体检：**前端产物是否存在且能被 API 托管、静态资源与 SSE 端点连通、真实账号登录态抽检** —— 原先散落的"网页测试 / live_test"全部收口于此 |
| `--raw` | 输出原始探针结果（HTTP 状态、耗时、命令回显）供排障 |
| `--fix` | 可安全自愈的项（缺目录、未迁移的 DB、过期的前端引用） |

**禁止出现的子命令**（评审 + CI 双向把关）：
- ❌ 测试类：`smoke-test`、`live-test`、`test-*`、`selftest` → 归 `pytest`；
- ❌ 校检类：`check-style`、`check-deploy`、`verify-*` → 归 `ruff` / `biome` / `cx doctor`；
- ❌ 一次性诊断类：`diag-*`、`check-pending`、`check-snapshot` → 归网页与 API 查询，或直接删；
- ❌ 网页测试类：`preview-page`、`e2e-*` → 归 `cx doctor --full` 与前端 dev server。

输出用 `rich`（表格/进度/彩色日志）。**同一容器**保证 CLI 与 API 行为一致（同一份限速、同一套事件、同一份审计、同一份数据可见性过滤）。

### 5.10 零硬编码原则与配置中心

**唯一判定标准**：凡"会因用户、环境、课程或时间而变"的东西，都不许以字面量形式存在于代码里。

| 类别 | 例子 | 归属 | 理由 |
| --- | --- | --- | --- |
| 协议事实 | `mooc1.chaoxing.com` 等端点路径、参数名 `playingTime`、卡片字段候选 `ATTR_CANDIDATES` | **代码** | 学习通的协议知识，改它等于改实现，应受类型与测试保护 |
| 结构常量 | 表名、路由前缀、枚举成员、哈希算法名 | **代码** | 改动即重构 |
| 可调参数 | 倍速、心跳区间、并发数、限速、超时、日志级别、模型与温度 | **DB 覆盖层**（env 仅作初始值） | 不同账号 / 机器 / 时段的最优值不同 |
| 业务决策 | **刷哪些课**、用哪些账号、刷课计划与顺序、自动提交开关、提示词文本 | **仅 DB**，代码中不得出现任何默认业务值 | 这是用户数据，不是配置 |
| 身份与权限 | 用户、角色、权限节点、会话 | **仅 DB** | 用户数据 |
| 密钥 | LLM API key、Fernet key、打码平台 key | **env / DB 加密列**，写后永不回显 | 敏感 |

**三条可执行红线**（进 CI，不靠自觉）：
1. 业务代码禁止 `import os` / 直接读环境变量 —— 一律经 `SettingsService.current()`（`ruff` 自定义规则 + `tests/test_no_hardcode.py` 扫描）；
2. 禁止字面量账号 ID / 课程 key / 模型名 / 提示词：测试扫描源码，命中"长数字 ID 常量""白名单外的模型名字符串""多行中文提示词"即失败；
3. 前端禁止硬编码权限节点、配置字段、枚举文案 —— 全部由 `/api/v1/meta/*` 下发（Biome 规则 + 评审）。

**配置中心**（`core/services/settings.py`）：

```python
class SettingsService:
    """三层来源：代码默认 → env/.env → DB 覆盖（运行时可变）。"""

    async def current(self) -> RuntimeSettings: ...  # 5s TTL 缓存 + 变更事件立即失效
    async def patch(self, changes: Mapping[str, Any], *, actor: User) -> SettingsDiff: ...
    def schema(self) -> list[SettingSpec]: ...  # key/type/default/group/sensitive/restart_required
```

- **写路径**：`PATCH /settings` → pydantic 校验 → 落 `settings_overrides` → 发 `SettingsChanged` 事件（同时进审计）→ 缓存失效；
- **热更新语义**：worker 在**单元边界**重新取配置；可热更项（并发、限速、心跳参数、模型、日志级别）立即生效，`restart_required=True` 的项（host/port、data_dir）由 API 在响应里列出、前端提示"重启生效"；
- **前端不写死表单**：`GET /settings/schema` 返回元数据，前端**动态渲染** —— 后端新增一个配置项，前端零改动；
- **敏感值**：写时加密落库，读时返回掩码（`sk-***`），`GET /settings` 与审计日志都不得回显原值。

**提示词同样入库**：`prompts` 表（key / content / version / updated_by），改完即生效，历史版本进审计 —— 与"禁止硬编码"同构。

### 5.11 API 模块化规范与路由清单

**组织规范（六条，CI 可查）**：
1. **一资源一包**：`routers/courses/` 是包而非单文件；包内按动作拆（`crud / scan / selection / detail`），包 `__init__.py` 组装 `APIRouter(prefix="/courses", tags=["courses"])` 并 `include_router` 子模块；
2. **单文件 ≤ 200 行**，超限即拆（`scripts/check_router_size.py` 进 CI）；
3. **handler ≤ 15 行**：只做"取依赖 → 调 `core.services.*` → 返回 DTO"；**业务逻辑不得出现在 router 里**；
4. `prefix` / `tags` / 鉴权依赖在**包级声明一次**，子模块只写路径尾段；
5. 写操作**自动进审计**（中间件统一处理，handler 不手写）；
6. 所有 DTO 落在 `api/schemas/<domain>.py`，与 router 包同名同构。

**路由总表（≈85 条）**：

| 分组 | 端点 |
| --- | --- |
| `auth` | `POST /auth/register` · `GET /auth/challenge` · `POST /auth/login` · `POST /auth/logout` · `GET /auth/me` · `POST /auth/password` |
| `users` | `GET·POST /users` · `GET·PATCH·DELETE /users/{id}` · `POST /users/{id}/password` · `GET /users/{id}/sessions` · `DELETE /users/{id}/sessions/{sid}` |
| `roles` | `GET·POST /roles` · `PATCH·DELETE /roles/{id}` · `GET /permissions`（节点树） · `PUT /roles/{id}/permissions` · `PUT /users/{id}/roles` |
| `accounts` | `GET·POST /accounts` · `GET·PATCH·DELETE /accounts/{id}` · `POST /accounts/{id}/enable\|disable\|login\|logout\|cookie` · `GET /accounts/{id}/status` |
| `account-groups` | `GET·POST /account-groups` · `GET·PATCH·DELETE /account-groups/{id}` · `PUT /account-groups/{id}/members`（虚拟账户组：批量启停 / 批量建计划 / 共享可见性） |
| `courses` | `GET /courses`（账号/关键词/已选/含视频/含题 筛选+分页） · `POST /courses/scan` · `GET /courses/{id}` · `PATCH /courses/{id}`（选择·优先级·备注·启用） · `DELETE /courses/{id}` · `POST /courses/bulk`（批量选择/取消/删除） · `GET /courses/{id}/chapters` · `POST /courses/{id}/refresh` |
| `study` | `GET·POST /study/plans` · `GET·PATCH·DELETE /study/plans/{id}` · `POST /study/plans/{id}/run\|pause\|reset` · `GET /study/sessions` · `GET /study/records` · `GET /study/progress` · `GET /study/report` |
| `quiz` | `GET /quiz/attempts` · `GET /quiz/attempts/{id}` · `POST /quiz/attempts/{id}/submit` · `GET·POST /quiz/cache` · `DELETE /quiz/cache/{id}` · `POST /quiz/ask` |
| `stats` | `GET /stats/overview`（KPI） · `GET /stats/heatmap` · `GET /stats/users` · `GET /stats/courses/{id}` |
| `settings` | `GET /settings/schema` · `GET /settings` · `PATCH /settings` · `POST /settings/reset` · `GET·PUT /settings/prompts` |
| `logs` | `GET /logs`（`stream=app\|study\|quiz`） · `GET /system/audit` |
| `stream` | `GET /stream/events` · `GET /stream/logs`（SSE） |
| `system` | `GET /system/health` · `GET /system/doctor` · `GET /system/endpoints` · `GET /system/tasks` · `POST /system/tasks/{id}/cancel` |
| `meta` | `GET /meta/enums` · `GET /meta/permissions` · `GET /meta/point-kinds`（前端下拉/标签的唯一来源） |

> **"刷什么"的完整链路（替代一切硬编码）**：`POST /accounts` 建账号 → `POST /courses/scan` 从学习通拉课程 → `PATCH /courses/{id}` 或 `POST /courses/bulk` 选定 → `POST /study/plans` 建计划（账号 + 课程集合 + 策略：视频/测验/文档开关、倍速、顺序）→ `POST /study/plans/{id}/run` 执行。全程不接触代码与配置文件。

### 5.12 Chaoxing User Registry（会话注册表）

即使不用 httpx 的 cookie jar，也必须有一个**规范化的运行时注册表**管住"超星用户"的会话状态——否则 cookie 会散落到各处被随手改。

```python
class SessionRegistry:
    """account_id → SessionState 的唯一真相源；生命周期与并发纪律都收在这里。"""

    def get(self, account_id: int) -> SessionState: ...
    def acquire(
        self, account_id: int
    ) -> AbstractAsyncContextManager[SessionState]: ...  # 账号级互斥
    async def invalidate(self, account_id: int) -> None: ...
    async def persist(self, account_id: int) -> None: ...  # 节流落库（cookie_enc / session_state）
    async def restore(self) -> None: ...  # 启动时按 DB 恢复可用会话
    def snapshot(self) -> Mapping[int, SessionSummary]: ...  # 供 /system 与 SSE 观测
```

职责边界（硬性）：
- `SessionState`（cookie 集合、UA、Referer、`enc`/`dtoken`/`ktoken` 等令牌、最近校验时间）**只能在 registry 内读写**；业务代码拿不到裸 cookie —— 从根上杜绝"某处偷偷改 header"的散弹式修改；
- **账号级互斥**：同一账号同时只允许一个刷课单元运行（`acquire()` 即锁）；账号之间的并发由调度器按 `MAX_ACCOUNT_CONCURRENCY` 控制；
- **状态变更发事件**：`SessionStateChanged(account_id, status)` → 事件总线 → SSE → 前端账户栏的登录态圆点；
- **持久化节流**：cookie 几乎每次响应都变，只在"确有变化且距上次落库 > N 秒"时才写库，避免每请求一次写。

### 5.13 测试哲学：只写真会炸的用例

**总原则**：每一条用例都必须能回答"**它对应哪次真实现象或哪份真实抓包**"。写不出出处的，不写。

- **禁止为覆盖率造测**：不测 getter/setter、不写"函数被调用了"式断言、**不设覆盖率门禁**（覆盖率只作观察指标输出）；
- **用例三类来源**：① legacy 抓包产物转 fixture（`data/debug/*.html`、`captured_*.json`、`cards_*.html`）；② legacy 事故复盘（jobid 双卡片合并、`jtoken` 缺失导致 200 假成功、3004 换票、字体反串、**已批阅测验误判**——即 worker.py 那条未提交修复）；③ 新架构固有边界（SSE 断连重连与 `Last-Event-ID` 续传、SQLite 写并发、argon2 慢哈希下的登录限速、时钟偏移、非安全上下文下 `crypto.subtle` 缺失、多账号 cookie 串号）；
- **反面样例清单**（评审直接打回）：`test_schema_defaults`、`test_returns_not_none`、mock 满堂红却什么都没验证的"单元测试"；
- **端到端只有一个**：真实账号的 `cx study run`，人工触发、不进 CI（学习通不可控，CI 里跑会带出风控）；
- 每个测试文件头部用一行注释标明来源，例：`# 来源：2026-09 生产事故 —— 已批阅测验误判（45 题）`。

### 5.14 协议覆盖目标：重新抓包 + 忠实还原

**目标不是"够用就行"，而是把刷课链路上真实存在的包与 API call 尽可能还原齐全。**

- **抓包工具链进 CLI**：`cx dev capture proxy`（本机代理式抓包）、`cx dev capture browser`（Playwright 网络记录导出 HAR → fixture）、`cx dev capture diff`（新抓到的请求 vs `client/` 已建模请求，输出**未建模调用清单**）；
- **未建模调用是一等公民**：自动生成 `docs/PROTOCOL_COVERAGE.md`（端点 → 是否建模 → 校准状态 → 是否有 fixture），**CI 检查不得倒退**；
- **必须建模齐全的面**：登录态与用户信息、课程列表、章节树、任务点卡片、视频心跳全链路（`dtoken`/`enc`/`rt` 全参数）、测验（换票 → 明文题 → 提交 → 回执）、考试、文档/PPT/阅读、讨论与回复、进度统计、人机验证（人脸/滑块）的触发与上报；
- **对账方式**：拿"真人完整操作一遍"的抓包逐条核对；缺一条必须在 `PROTOCOL_COVERAGE.md` 写明"为什么没还原"（例如无可信触发入口），而不是默默略过。

---

## 6. 前端：完全重写（推翻旧页面）

### 6.1 技术栈与工程约束

| 项 | 选择 | 说明 |
| --- | --- | --- |
| 构建 | **Vite** + React 19 + TS（strict） | `web/` 独立包，产物 `web/dist` 由 FastAPI 托管（SPA fallback） |
| UI | **shadcn/ui** + Tailwind v4 | 组件源码入库，按需改；主题用 CSS 变量 |
| 数据 | **useSWR** + **ky** | ky 实例统一 `prefixUrl=/api/v1`、`credentials: include`、错误拦截、401 跳登录 |
| 实时 | **SSE**（原生 `EventSource`） | ⛔ 禁止 `setInterval/setTimeout`；见 6.3 |
| 路由 | React Router v7（data router） | 路由级懒加载 |
| 表格/图表 | TanStack Table + Recharts（热力图用日历格自绘） | 不引重型 UI 套件 |
| 类型 | `openapi-typescript` 从后端 OpenAPI 生成 | 前后端契约同源 |
| 校验 | **Biome**（lint + format 一体，替代 ESLint + Prettier） | 一条命令 `pnpm dlx @biomejs/biome check --write .` |
| 布局 | Tailwind 断点 + shadcn `Sheet/Drawer/Tabs` | 三栏工作台在窄屏折叠，见 §6.7 |

**禁止定时器的可执行约束**（写进 `biome.json`，Biome 统一 lint + format，不再用 ESLint/Prettier）：
```jsonc
// biome.json —— 规则名若随版本变化，以 `pnpm dlx @biomejs/biome explain noRestrictedGlobals` 输出为准
{
  "linter": { "enabled": true, "rules": { "style": { "noRestrictedGlobals": { "level": "error",
    "options": { "deniedGlobals": {
      "setTimeout":  "禁止业务定时器：用 SSE + SWR mutate 驱动",
      "setInterval": "禁止业务定时器：用 SSE + SWR mutate 驱动"
    } } } } } },
  "formatter": { "indentStyle": "space", "lineWidth": 100 }
}
```

### 6.2 页面划分与布局

```
/login               登录（sha256 → C → 会话 cookie）
/                    Dashboard：用户 stats 面板 · 刷课热力图 · 全局 KPI
/courses             课程工作台（三栏 + 底栏，见下）
/quiz                刷题：题库/答题记录/置信度筛选/手动提交
/users               用户管理：用户·角色·权限节点树·审计
/settings            设置：Chaoxing 参数（倍速/心跳抖动/限速）、AI 配置、观测配置
```

**课程工作台布局**（严格按 5.4 要求）：
```
┌────────────┬──────────────────────────────────────┬────────────┐
│ 左侧边栏    │ 中间主区                              │ 右侧控制面板 │
│ 账户搜索    │ ┌ 课程详情（章节树/任务点状态）        │ 刷课控制     │
│ 账户列表    │ └ 课程选择（可刷列表 + 勾选）          │ ·启动/暂停/重置│
│ 切换当前账号│                                       │ ·并发与速率  │
├────────────┴──────────────────────────────────────┴────────────┤
│ 底部日志面板（固定高度 240px，SSE 实时，拖拽调高，可折叠）          │
└─────────────────────────────────────────────────────────────────┘
```

### 6.3 实时数据：SSE 驱动，零定时器

- **`useSWRSubscription`** 是 SWR 官方给 SSE/WebSocket 的钩子：`{ data, error } = useSWRSubscription('/stream/events', sseSubscriber)` —— 实时数据**根本不需要轮询**；
- **REST 数据的更新时机**：SSE 事件到达 → 精准 `mutate(key)`（或直接用事件 payload 做 `mutate(key, updater, {revalidate:false})` 乐观更新），例如 `task_point.finished` → `mutate(['courses', accountId])`；
- **断线重连**：`EventSource` 浏览器内建重连（含 `retry:` 建议、`Last-Event-ID` 断点续传，服务端按 `id:` 补发缓冲事件）——不需要任何手写重试定时器；
- **ky 的 `retry` 仅在网络层瞬断使用**（库内实现，非业务定时器）；
- **明确边界（诚实说明）**：`setTimeout` 禁令针对**业务与数据刷新**。CSS/Web Animations 承担动画；服务端 SSE 的 keep-alive ping（默认 15 s）是协议层需求，用 `asyncio.wait_for` 实现，不属于前端定时器。

### 6.4 权限驱动 UI

- 登录后 `GET /auth/me` 返回 `{user, roles, permissions[]}`；
- `usePerm()` + `<Can perm="study:start">` 决定菜单/按钮可见性；后端仍逐个校验（前端隐藏只是体验）；
- 用户管理页支持：建用户、分配角色、勾选权限节点（树形）、查看审计流。

### 6.5 元数据驱动（与 §5.10 配套）

- 权限节点、枚举、任务点类型、配置表单 —— **全部从 `/api/v1/meta/*` 与 `/settings/schema` 拉取**，前端零硬编码；
- 设置页表单由 `SettingSpec[]` **动态渲染**（`bool→Switch`、`enum→Select`、`number→Input`、`secret→掩码输入框`、`restart_required→角标`）；
- 新增权限或配置项只改后端：前端不发版也能显示（除非需要全新控件类型）。

### 6.6 课程工作台的 CRUD 交互（对应 §5.11 的 `courses` / `study` 路由）

| 区域 | 交互 | 命中接口 |
| --- | --- | --- |
| 左侧账户栏 | 搜索、切换当前账号、显示登录态/并发状态 | `GET /accounts`、`GET /accounts/{id}/status` |
| 中间主区 | 课程列表（筛选/分页）→ 勾选、设优先级、写备注；展开章节树与任务点状态 | `GET /courses`、`PATCH /courses/{id}`、`POST /courses/bulk`、`GET /courses/{id}/chapters` |
| 中间主区（动作） | 重扫学习通、刷新单课 | `POST /courses/scan`、`POST /courses/{id}/refresh` |
| 右侧控制面板 | 建/选刷课计划，启停、重置、看运行态 | `GET·POST /study/plans`、`POST /study/plans/{id}/run\|pause\|reset`、`GET /study/sessions` |
| 底部日志面板 | 实时日志（按流/级别过滤，固定高度可拖拽折叠） | `GET /stream/logs`（SSE） + `GET /logs`（历史回填） |

### 6.7 响应式布局（三档断点）

| 断点 | 目标 | 课程工作台 | Dashboard | 其它 |
| --- | --- | --- | --- | --- |
| `≥1280px` 桌面 | 主力场景 | 三栏并置 + 底部 240px 日志面板（可拖拽、可折叠） | KPI 三列 + 热力图整年 + 用户 stats 并排 | 表格全字段 |
| `768–1279px` 平板/窄桌面 | 笔记本分屏 | 左栏折叠为抽屉（`Sheet`），右栏折叠为「控制」浮层，主区占满；日志面板降到 160px | KPI 两列，热力图按半年折叠 | 表格隐藏次要列 |
| `<768px` 手机 | 应急查看 | **单列 + 底部 Tab**（账户/课程/控制/日志 四页签）；日志面板改为全屏 `Drawer` | KPI 单列堆叠，热力图按季度折叠、可切换账号 | 表格切卡片列表视图 |

实现要点（与 §6.3 一致，**不写 JS 监听**）：
- 全部用 Tailwind 断点 + shadcn 的 `Sheet/Drawer/Tabs`，**禁止 `window.addEventListener('resize')`/`matchMedia` 手动监听**；
- 触控目标 ≥ 44px；底部日志面板在小屏用 `Drawer` 而非固定高度 div；
- 热力图在窄屏用"季度/半年折叠 + 横向滚动"而不是缩小格子（保持可读性）；
- 播放/进度等长任务的状态，在小屏以顶部粘性状态条呈现（SSE 驱动，不用定时器）。

---

## 7. 数据模型（v2 表清单，约 20 张）

| 域 | 表 | 关键列 |
| --- | --- | --- |
| 身份 | `users` | id, username(uniq), password_hash(argon2), is_active, created_at |
| | `sessions` | token_hash, user_id, ip, ua, created_at, last_seen, revoked_at |
| | `roles` `permissions` `role_permissions` `user_roles` | 权限节点 code（**含数据范围语义**：`account:read:own` / `account:read:all`）、角色名、绑定关系 |
| 账号 | `accounts` | ★ **不强制绑定平台用户**：`owner_user_id`（可空 = 未绑定 / 管理员共享）, username, password_enc(Fernet), cookie_enc, enabled, priority, session_state(JSON), last_check_at |
| | `account_groups` | ★ 虚拟账户组：id, name, owner_user_id（可空 = 系统组）, visibility(public/private), note |
| | `account_group_members` | group_id ↔ account_id（多对多） |
| 课程 | `courses` | ★ **可 CRUD 的业务实体**：course_key, title, account_id, `selected`, `priority`, `note`, `enabled`, `scanned_at`, `deleted_at`(软删) |
| | `chapters` `task_points` | 章序号；point_id, kind, status, progress, sequence, last_error, retry_count |
| 计划 | `study_plans` | ★ name, account_id, `strategy`(JSON: 视频/测验/文档开关·倍速·顺序), enabled, created_by |
| | `study_plan_courses` | plan_id ↔ course_id 多对多 + 执行顺序（"刷什么"的权威来源） |
| 刷课 | `study_sessions` `task_events` | 一次刷课会话（起止/账号/计划）；任务点事件流水（心跳数/耗时/结果） |
| 刷题 | `quiz_attempts` `answer_cache` | 题目指纹, 答案来源, 置信度, 提交回执；缓存题→答案 |
| 配置 | `settings_overrides` | ★ key, value_json, updated_by, updated_at（DB 覆盖层，见 §5.10） |
| | `prompts` | ★ key, content, version, updated_by, updated_at（提示词入库，可热改） |
| 统计 | `activity_daily` | 日期×账号：完成任务点、观看秒数、答题数（热力图与 KPI 数据源，SSE 增量维护） |
| 审计 | `audit_logs` | user_id, action, resource, resource_id, ip, ua, result, request_id, ts |
| 系统 | `jobs` | 手动触发的任务记录（替代旧 `tasks` 表） |

> 旧库 7 表的映射见 §8.1；`answer_cache`/`events` 直接继承语义，`accounts` 迁移需保留 `SECRET_KEY` 兼容（旧 Fernet 密文可直接解）。

---

## 8. 迁移策略

### 8.1 旧数据（`data/chaoxing.db`）
1. **不重写旧库**：新库 `data/app.db` 由 Alembic 从零建立；
2. `cx db import-legacy --legacy-path data/chaoxing.db`：
   - `accounts` → `accounts`（沿用旧 `SECRET_KEY` 解密后**用同一 Fernet 重新加密入库**；`owner_user_id` 迁移后**留空** —— 旧库没有平台用户概念，属主绑定由管理员在网页上按需建立；`SECRET_KEY` 进 `.env`，缺失则跳过并警告）；
   - `courses` / `task_points` → 结构不同，**不迁移**：`cx course scan` 重扫一遍更干净（学习通为唯一真相源，本地只是缓存）；
   - `answer_cache` → 按题干指纹迁移（可省一次模型调用，代价极低）；
   - `progress_snapshots` / `events` → 丢弃（历史价值低，新表结构不同）；
3. 迁移完成打点审计：`audit_logs(action="db.import_legacy", result=…)`。

### 8.2 旧 `tools/` 61 脚本的归置（**禁止全量搬家**）

原则：**CLI 不是脚本的收容所。** 只有"后端本该有的运维 / 开发者命令"才进 `cx`，其余按语义重新落位。

| 去向 | 数量 | 具体 |
| --- | --- | --- |
| **进 CLI（按语义重组，不 1:1 搬）** | ≈18 | `login_smoke`→`cx auth login`；`cookie_from_browser`→`cx auth import-cookie`；`list_courses`/`scan_points`→`cx course list\|scan`；`batch_import`→`cx account import`；`disable_account`/`toggle_account`/`server_fix`→`cx account enable\|disable`；`reset_running`/`reset_failed`/`reset_attempts`→`cx study reset`；`probe_*`(7)→`cx dev probe *` |
| **进测试套件（fixture 供料 + 回归）** | ≈9 | `capture_heartbeat` `capture_quiz` `capture_submit` `compare_heartbeat` `diff_live_vs_capture` `replay_capture` `test_qa` `test_document` `test_font_decrypt` → `tests/fixtures/`（抓取动作由 `cx dev capture` 驱动） |
| **被工具链取代（不写命令）** | ≈7 | `selftest`→pytest；`check_style`→ruff/Biome；`smoke_api`→pytest + `httpx.ASGITransport`；`live_test`/`check_deploy`/`verify_config`→`cx doctor [--full]`；`init_env`→`cx doctor --fix` |
| **提升为业务功能（走 API / 网页）** | ≈4 | `reply_discussions`→讨论任务点（core 服务 + API）；`assist_submit`→刷题页手动提交；`preview_page`→前端 dev server；`clean_illustrations`→数据修复任务 |
| **归档 / 删除** | ≈23 | `diag_*`(7) `diagnose_*`(3) `check_pending` `check_snapshot` `check_activity` `check_server_data` `diag_progress` `verify_enc` `dump_quiz_page` `analyze_quiz_page` `fetch_player_js` `fetch_reference` 等 —— 能力已由 `cx dev capture *` 覆盖，且结论早已沉淀为代码与测试；需要考古的进 `tools/archive/`（保留 git 历史 + README 说明"何时才需要跑"），其余直接删除 |

合计 61 项，逐条落表在 P6 完成并进 `docs/`（含"为何如此归置"的一句话理由）。

### 8.3 旧前端
`src/chaoxing/api/static/index.html` 直接删除；`web/` 从零建（Vite 脚手架 + shadcn init）。旧页面仅存在于 `legacy` 分支，需要考古时 `git show legacy:…`。

---

## 9. 阶段计划（每期可独立验收）

| 期 | 内容 | 交付物 | DoD（完成判据） | 估时 |
| --- | --- | --- | --- | --- |
| **P0** 地基 | 装 uv；`uv init` src-layout；**linter 三件套（ruff + mypy + import-linter）+ Biome 初始化**；`config.py` 分域 Settings；`cli/main.py` 空壳；日志初始化；`cx doctor` 雏形；**零硬编码扫描 + 路由尺寸检查脚本**；新分支 `v2`；**全程只用官方脚手架（§4.5）** | 可 `uv run cx doctor`、`uv run pytest` 的骨架 | 七条判据里的 1、3 达成；`scripts/ci.ps1` 一条命令全绿（含 Biome） | 0.5–1 d |
| **P1** client 层 | transport / **SessionRegistry** / model / api 按域重写；错误页识别；字体反串；**抓包工具链 `cx dev capture *`**；录制真实 fixture；生成 `PROTOCOL_COVERAGE.md` | 无任何 storage/api 依赖也能跑通"登录→课程列表→章节→卡片"；未建模调用清单可查 | 30+ fixture 参数化用例全绿（**每条注明真实来源**）；真实账号 `cx auth login` 一次通过；覆盖清单无"未知端点" | 4–5 d |
| **P2** storage | SQLAlchemy 模型 + Alembic + UoW + 仓储；**新增课程/计划/配置表**（`study_plans` `study_plan_courses` `settings_overrides` `prompts`）；`cx db upgrade`；legacy 导入 | 新库 schema 与迁移闭环 | `cx db upgrade` 空库建全表（≈20 张）；`cx db import-legacy` 成功迁移 accounts 与题库；迁移测试覆盖 | 2 d |
| **P3** core + obs | 事件总线、调度器、study/quiz/progress/stats 服务、四通道可观测落地 | **无 API 也能刷课**：`cx study run --account 1` | 真实账号刷完 1 视频 + 1 测验 + 1 文档；四类记录分别可查；事件总线背压测试通过 | 3–4 d |
| **P4** api 层 | app/容器/依赖/安全/中间件/SSE；**≈85 条路由按 §5.11 拆包**（auth/users/roles/accounts/account-groups/courses/study/quiz/stats/settings/logs/stream/system/meta）；**数据可见性过滤（`visibility.py`，管理员见全部）**；**配置中心与提示词 CRUD**；RBAC；OpenAPI 产出 | 完整后端 API + SSE + 可运行时改配置 | 权限元测试通过；**数据范围测试通过**（超管见全部 / 普通用户只见自己与共享组）；路由规范三项检查通过（一资源一包 / ≤200 行 / handler ≤15 行）；SSE 断线续传测试通过；OpenAPI 生成前端类型成功 | 3–4 d |
| **P5** 前端 | Vite+React+TS+shadcn+SWR+ky；**Biome 作唯一 lint/format**；五个页面 + 课程工作台三栏；**三档断点响应式（§6.7）**；SSE hooks；权限与数据范围 UI | 可用的新 SPA | **Biome 定时器禁令零违规**；三档断点（375/768/1280）手测通过；`pnpm build` 产物被 API 托管；五页面端到端手测通过（含 SSE 实时刷新） | 5–7 d |
| **P6** CLI + 运维 | **`cx` 命令树落地（两级嵌套，§5.9）**；`tools/` 61 项按 §8.2 五分类归置并落表；部署脚本 uv 化（systemd + nginx + 证书钩子）；`cx doctor [--full\|--raw\|--fix]` 完整 | 一套可复制的部署 SOP | 从空服务器到服务可用 ≤ 6 条命令；`cx doctor --full` 全绿；**CLI 中无任何测试/校检/诊断类子命令**（评审 + 脚本检查） | 1.5–2 d |
| **P7** 收尾 | 删 legacy 残件（`core/` `app/` `tools/` 旧版）；`ARCHITECTURE.md`；性能/内存复核；tag `v2.0.0` | 干净的 v2 仓库 | 七条判据全绿；3 账号并发常驻 < 260 MB | 1 d |

**总估时：约 20–28 个工作日**（单人）。这是**重写量级**（≈22k 行 → 预计 14–17k 行新代码 + 测试 + 抓包还原），不是迭代量级——先接受这个数字，再谈并行压缩。

**并行化建议**：P1（client）与 P2（storage）无依赖，可并行；P5（前端）可在 P4 的 OpenAPI 冻结后立即开工，与 P6 并行。

---

## 10. 风险与对策

| # | 风险 | 影响 | 对策 |
| --- | --- | --- | --- |
| 1 | **协议知识流失**（最大风险）：1025 行 parser 里的隐性规则（字段候选、错误页指纹、合并去重）重写时遗漏，表现为"某类课刷不动" | 高 | P1 **先录 fixture 后写代码**：把 legacy 的 `data/debug/*.html`、`cards_*.html` 与三个 `captured_*.json` 全部转成测试样本；每条 legacy 特判（jobid 合并、jtoken 缺失、3004 换票、字体反串）都有对应测试用例；P3 用真实账号逐课程类型回归 |
| 2 | **AI 依赖链路**：`openai` SDK 指向 DeepSeek 兼容端点时的结构化输出（`instructor`）对多选/判断题格式的适配度 | 中 | P0 就用 `uv add` 装好并跑一次真实问答冒烟（`cx dev probe ai`）；失败则退到 `response_format=json_object` + pydantic 校验，仍不达标再考虑 `litellm` —— 薄 Protocol 保证上层零改动 |
| 3 | **SSE + 多 worker**：事件总线是进程内的，多 worker 下订阅者只见自己进程的事件 | 中 | 明确约束 `workers=1`（沿用 legacy 约束）；若未来要横向扩，替换 `EventBus` 的后端为 Redis pub/sub（Protocol 已留位） |
| 4 | **SQLite 写并发**：SSE 广播 + 心跳落库 + 审计写入同时发生 | 中 | 单写者 `UnitOfWork` + 有界队列；压测用例（原 `test_db_lock.py` 思路）进 CI |
| 5 | **前端重写出现功能空窗** | 中 | P5 之前后端已可用（CLI + API），旧页面删除不影响运维（`cx study` 顶得住）；前端优先做课程工作台与 Dashboard |
| 6 | **`crypto.subtle` 只在安全上下文可用**：用 `http://192.168.x.x` 访问时 `crypto.subtle` 为 `undefined`，前端 sha256 直接崩 | 中 | 生产强制 HTTPS（已有证书钩子）；检测非安全上下文时**禁用登录入口并显式提示**，而不是静默降级为明文上传 |
| 7 | **范围膨胀**：重构期被"顺便加功能"拖死 | 高 | 严守 §11"不做清单"；新增功能一律进 backlog，不占 P0–P7 |
| 8 | **shadcn/Tailwind v4 与构建链磨合** | 低 | P5 第一天先用官方脚手架跑通 `build` 再写业务组件 |

---

## 11. 明确不做的事（本期范围外）

1. 不引入 Postgres / Redis / 消息队列 / K8s —— 单机 4C4G 单进程 SQLite 是既定目标；
2. 不做多租户 SaaS 化（用户体系是本机/小团队的 RBAC，不做组织/邀请/计费）；
3. 不做 GraphQL、不做 gRPC、不做 OpenTelemetry 全家桶（进程内 metrics + 四通道日志足够）；
4. 不做移动端/小程序/PWA；
5. 不改进任何"不可伪造环节"（人脸/滑块仍走人工或 Cookie 导入）；
6. ~~不在重构期新增业务功能~~ **（本条作废，rev 3）**：本次重构的协议面目标是**忠实还原** —— 重新抓包并覆盖刷课链路上大部分包与 API call（见 §5.14），围绕用户 stats / 课程 / 题目等与本项目相关的 API 都要实现，不接受"够用就行"式裁剪；仅**直播**这类无可信抓包依据的边角维持 backlog；
7. 不为 legacy 代码写兼容层（不 import 旧模块、不保留旧函数签名）。

---

## 12. 验收清单（P7 打勾）

- [ ] `uv sync --frozen` + `uv run cx doctor` 在干净环境一次通过
- [ ] `uv run pytest`（离线）全绿，覆盖 client/storage/core/api 四层
- [ ] `uv run ruff check` / `uv run mypy src` / `uv run lint-imports` 零问题
- [ ] `uv run cx study run` 真实账号跑通视频 + 测验 + 文档任务点，四类记录可查
- [ ] 前端五页面可用，SSE 实时刷新与断线重连验证通过，定时器禁令零违规
- [ ] 权限元测试通过（所有写路由均有 `require(...)`）；首个用户自动超管
- [ ] **零硬编码审计通过**：源码无字面量账号/课程/模型名/提示词；前端无硬编码权限与枚举（全部来自 `/api/v1/meta/*`）
- [ ] **"刷什么"全链路网页可 CRUD**：建账号（可不绑定平台用户）→ 扫描课程 → 选课 → 建计划 → 运行，全程不改代码与配置文件
- [ ] **协议覆盖清单（`docs/PROTOCOL_COVERAGE.md`）刷课链路全绿**，未建模调用为零或已登记原因
- [ ] **测试用例全部可追溯到真实现象 / 抓包**（抽查 10 条能说出出处）；无凑覆盖率用例；无覆盖率门禁
- [ ] **数据可见性正确**：超管可见全部账号及其绑定内容；普通用户只见自己的与共享组内的；无可见账号返回空集而非 403
- [ ] **三档断点响应式验证通过**（375 / 768 / 1280），窄屏下工作台与日志面板行为符合 §6.7
- [ ] **路由规范三项检查通过**：一资源一包 / 单文件 ≤ 200 行 / handler ≤ 15 行
- [ ] **CLI 无越界子命令**：不存在 `test-*` / `smoke-*` / `check-*` / `diag-*` / `e2e-*` / `preview-*`；网页与端到端检查全部由 `cx doctor --full` 承担
- [ ] **`pyproject.toml` 无手写依赖**：依赖段与 `uv.lock` 一致（`cx doctor` 漂移检查通过）
- [ ] linter 全绿：`uvx ruff check` + `uvx mypy` + `biome check`（前端唯一 lint 工具）
- [ ] 部署 SOP ≤ 6 条命令；`deploy/` 脚本已 uv 化
- [ ] `docs/ARCHITECTURE.md` + `README.md` 与实现一致（不再出现 legacy 那种文档漂移）
- [ ] 内存复核 < 260 MB（3 账号并发）
- [ ] `legacy` 分支冻结打 tag，`v2.0.0` 可发布

---

## 13. 执行后偏离登记（2026-10-11 核对）

本表记录"计划写的"与"实际长成的"差异及处置。**不是失败清单** —— 有些是刻意取舍。

| 计划 | 实际 | 处置 |
| --- | --- | --- |
| §3 `ai/` 包（provider / providers / search / engine） | 原为空包（LLM 调用在 `core/services/answers.py`） | **已收口（2026-10-11）**：`ai/{provider,prompts,factory,providers/{openai_sdk,fake}}`，`core` 只依赖 `LLMProvider` 协议（import-linter 契约钉住）。**检索层也补齐（2026-10-12）**：`ai/search.py`（DDG HTML + 假实现）+ 引擎在未命中且 `websearch_enabled` 时拼片段；检索失败/超时只记日志、照常作答 |
| §5.11 ≈85 条路由 | 曾 42 条；`study/plans`、`quiz/attempts`、`logs`、`meta/*`、`system/{doctor,tasks,endpoints}`、`settings/prompts` 缺失 | **已补齐（2026-10-11 六组 + 2026-10-12 `quiz/attempts`、`study/{pause,resume,runs}`）**：路由面覆盖 §5.11 的核心承诺 |
| `cx study {pause,reset,logs}`（原 backlog） | 曾缺失：刷课跑起来停不下来，本地进度也重置不了 | **已收口（2026-10-12）**：`study_runs` 控制表（Alembic `a61dcdcb05af`）+ 运行器检查点（任务点边界 / 每次心跳）+ CLI 四个命令 + 网页暂停/继续；中止会如实写进报告（`aborted`/`abort_reason`）而不是静默跳过 |
| 前端类型（原手写 DTO） | 曾靠手抄，本期已踩过一次字段名不一致（`strategy_keys`） | **已收口（2026-10-12）**：`scripts/gen_api_types.sh` 从应用对象生成 OpenAPI → `web/src/lib/api-types.ts`；`hooks.ts` 改为生成类型别名；CI 重生成后 `git diff --exit-code` 挡漂移 |
| §5.10 `prompts` 表可热改 | 曾"表已建但无读取方"，提示词来自 `AiSettings` | **已补齐（2026-10-11）**：`PromptService` + `settings/prompts` + provider 按库内文本构造（改提示词不需重启） |
| §5.6 app 日志查询 / SSE 通道 | 只有领域事件流 `GET /api/events`；app 日志只落文件 | 接受现状（ARCHITECTURE 已按实际改写口径） |
| §5.11 路由前缀 `/api/v1` | 实际 `/api` | 接受现状（前后端一致） |
| §5.9 `cx dev probe {endpoints,heartbeat,quiz,font,ai}` | `{endpoints,coverage,heartbeat}`；`capture {browser,diff,proxy,sanitize}` | heartbeat 已补齐（2026-10-11）；其余按需 |
| §3 `python -m chaoxing` 入口 | 缺失 | **已补齐**（2026-10-11） |
| §5.14「未建模调用 0 条」 | 曾是**空对账**（录制 0 条） | **已补齐**：脱敏录制入库 + 守卫测试 + 注册表对齐（32 端点 / VERIFIED 13） |
| §4.1 / §5.9 的抓包与网页测试工具链 | 原先由 Playwright（`browser` extra）承担 | **改由 orca-cli 驱动**（2026-10-11）：`cx dev capture browser` 与 `scripts/web_check.sh`；`playwright` 依赖与自建浏览器驱动一并移除 |
| §5.6「四通道」中的日志通道 | 底部日志面板消费的是领域事件，不是 app 日志流 | 接受现状（面板已在 UI 里如此标注） |

注：§4 / §9 里出现的 `scripts/ci.ps1` 字样**不改** —— 那是当时的执行记录。本 harness 的命令环境是 bash
（PowerShell 不在 PATH），门禁入口为 `bash scripts/ci.sh`，与 `ci.ps1` 跑同一组门禁。

---

## 附：本计划涉及的关键决策速查

| 决策点 | 结论 |
| --- | --- |
| 包管理 | uv（**本机尚未安装，P0 第一步**），锁文件入库，一切通过 `uv run` |
| `sqlalchemy[sqlite3]` | ❌ 该 extra 不存在 → 用 `sqlalchemy[asyncio,aiosqlite]` |
| LLM 客户端 | ❌ 弃用 `vercel-ai-sdk`（实测 0.0.1.dev10 实验版）→ ✅ `openai` 官方 SDK + `instructor`；薄 `LLMProvider` Protocol 仅为测试替换，**禁止自造 HTTP/重试/流式层** |
| 多会话共享 httpx | 单 `AsyncClient`（无 client 级 cookie jar）+ per-request cookie + 手动吸收 302 链 Set-Cookie；会话状态由 **SessionRegistry** 统一管理 |
| 账号模型 | **不强制绑定**平台用户 ↔ 超星账号：属主可空、可随时绑定/解绑；**虚拟账户组**作批量操作与共享的粒度 |
| 权限 | 节点含**数据范围语义**：超管 / `account:read:all` 可见全部账号及其绑定内容；仓储层强制过滤，前端不筛 |
| 网页 | 全部推翻重写：Vite + React 19 + TS + shadcn + useSWR + ky；旧 `index.html` 直接删除 |
| 响应式 | 三档断点（375 / 768 / 1280）：桌面三栏 → 平板抽屉 → 手机单列 + 底部 Tab；纯 CSS，不写 JS 监听 |
| 实时 | SSE；前端用 `useSWRSubscription` + `mutate`；**Biome** 禁 `setTimeout/setInterval` |
| linter | Python：`ruff` + `mypy` + `import-linter`；前端：**Biome**（lint + format 唯一工具，替代 ESLint + Prettier） |
| 测试 | 只写**真实会炸的 edge case**，每条可追溯到抓包或事故；**无覆盖率门禁**；E2E 仅真实账号手动跑 |
| 协议面 | **忠实还原**：重新抓包 + `cx dev capture diff` 未建模清单 + `PROTOCOL_COVERAGE.md`，CI 不许倒退 |
| CLI | 只收后端该有的命令（两级嵌套，如 `cx auth login`）；**测试 / 校检 / 诊断 / 网页测试类子命令一律不进 CLI**，`tools/` 不许 1:1 搬家（§5.9、§8.2） |
| 脚手架 | 能生成的一律不手写：`uv init/add/run`、`pnpm create vite`、`pnpx shadcn add`、`uvx alembic/ruff`、`pnpm dlx biome`（§4.5） |
| 硬编码 | 零容忍：业务决策（刷什么课/什么模型/什么提示词）**仅存 DB**，代码里只有协议事实与结构常量；CI 扫描兜底 |
| 配置 | 三层来源（代码默认 → env → DB 覆盖）+ `SettingsService`，可热更；前端表单由 `/settings/schema` 动态渲染 |
| API | 一资源一包、包内按动作拆文件；单文件 ≤ 200 行、handler ≤ 15 行；≈85 条路由覆盖全部 CRUD |
| 口令 | 前端 `sha256(password)`（WebCrypto）→ 后端 Argon2id；**不做伪 PAKE**，安全边界靠 TLS + HttpOnly + 限速 |
| 可观测性 | 四通道物理隔离：app 日志 / 刷课记录 / 刷题记录 / 行为审计 |
| 数据库 | SQLAlchemy 2.0 async + Alembic，新库从零建，legacy 只迁账号与题库缓存 |
| 分支策略 | `legacy` 冻结为参照；重构在 `v2` 分支推进，P7 收尾后合入 `main` 并打 tag |
