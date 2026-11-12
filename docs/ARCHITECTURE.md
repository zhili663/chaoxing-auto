# 架构说明

> 这份文档是 `chaoxing-auto` v2 的架构契约。它描述**当前代码的实际情况**，
> 而不是某个理想设计；每条约束后面都注明"谁来保证它不被破坏"。

## 一、五层结构与依赖方向

```
        ┌──────────────────────────────────────────────┐
        │  api/ (FastAPI)   cli/ (click)   web/ (SPA)  │  ← 入口层
        └────────────────────┬─────────────────────────┘
                             │
        ┌────────────────────▼─────────────────────────┐
        │  core/   领域服务：账号/课程/计划/刷课/答案/统计  │
        └────────────────────┬─────────────────────────┘
                             │
        ┌────────────────────▼─────────────────────────┐
        │  client/ (学习通协议)        storage/ (数据)   │
        └────────────────────┬─────────────────────────┘
                             │
        ┌────────────────────▼─────────────────────────┐
        │  obs/ (事件与日志)         utils/ (纯工具)      │
        └──────────────────────────────────────────────┘
```

**依赖只能向下**，且由 `import-linter` 在 CI 里机器强制：

| 契约 | 含义 | 违规例子（真实发生过的） |
| --- | --- | --- |
| `core` 不得依赖 `api` / `cli` | 领域逻辑不认 HTTP 与命令行 | — |
| `client` 必须能独立运行 | 协议层不认数据库与领域概念 | — |
| `obs` 是最底层 | 它只吃事件值对象 | — |
| `storage` 不得依赖领域与入口 | 仓储不知道"刷课"是什么 | 仓储直接吃了 `client` 的课程模型 |
| （间接） | `utils` 也不得依赖上层 | `utils.crypto` 的异常继承自 `client` 的异常基类，把 `storage` 拖下水 |

最后两条是**间接依赖**被抓出来的实例——这正是分层契约必须在 CI 里机器化的理由：
跨两层的传递依赖靠人眼 review 基本发现不了。

## 二、装配只有一个地方

`core/container.py` 是唯一的装配点（允许横跨各层的例外）：

```
配置 → 密钥 → 引擎/事务 → 事件总线 → 传输层 → 会话注册表 → 领域服务
                                                          ↓
                              CLI 命令 / FastAPI lifespan / 测试 三处复用
```

因为三处复用同一份装配，**限速、事件、审计的口径在 CLI 与 API 上必然一致**——
不会出现"CLI 能刷、API 不能刷"这种口径分裂。

## 三、协议层（client/）

- **端点注册表**（`endpoints/`）把每个接口的 URL、必需参数、`calibration` 标注集中描述；
  `Calibration.VERIFIED` = 抓包确认过，`TEMPLATE` = 结构已知但未逐字验证。
  `cx dev probe endpoints` 随时可以重新核对。
- **签名与令牌**（`signature.py`）集中在一处，独立文件独立测试 ——
  算法写错的表现是线上全部 403，没有任何缓冲余地。
- **解析层是纯函数**（`parser/`），可喂 fixture 离线回归。
- **API 模块无状态**，会话按请求传入；一个 `httpx.AsyncClient` 服务多个账号，
  cookie 由 `SessionState` 持有（不用 client 级 jar）。

## 四、数据层（storage/）

- SQLite + aiosqlite，连接级 PRAGMA（WAL / `busy_timeout` / **`foreign_keys=ON`**）。
  SQLite 默认**不**强制外键，不显式打开约束就形同虚设。
- **单写者**：`UnitOfWork.begin()` 拿进程内写锁。SQLite 的写事务是数据库级串行的，
  放任并发就会在 `busy_timeout` 之后开始报 `database is locked`。
- schema 演进走 Alembic（`uvx alembic revision --autogenerate` + `render_as_batch`，
  因为 SQLite 不支持 `ALTER COLUMN`）。
- 仓储只接 `AsyncSession`，自己不建连接、不管事务 —— 事务边界只有一个地方。

## 五、四通道可观测

| 通道 | 载体 | 谁写 | 谁读 |
| --- | --- | --- | --- |
| app 日志 | structlog（JSON 行） | `DomainLogObserver` | 文件 / 采集 · `GET /api/logs` · SSE `/api/stream/logs`（`system:logs`） |
| 刷课记录 | `task_events` | `TaskEventRecorder` | `cx study logs` / `/api/study/*` |
| 刷题记录 | `quiz_attempts` | quiz 服务 | 网页刷题页 |
| 行为审计 | `audit_logs` | 审计中间件（写操作） | `/api/audit`（`system:audit`） |

**心跳进度故意不落库**（每 12~20 秒一条会写爆库），它只走 SSE 与日志。

**为什么 `obs/` 里只有"事件 + 日志"**（而不是再加一个 metrics 模块）：三件事各有更合适的家 ——

- **计数/指标**：`EventBus` 自带（`published` / `subscribers` / `dropped` / `handled` / `errors`，
  `GET /api/system/overview` 直接读它），够回答"总线健康吗"；
- **行为审计**：`core/services/audit.py` —— 它要写库、要知道"谁做的"，是领域概念，不是基础设施；
- **实时广播**：`api/routers/stream` —— 它要吃 Cookie 鉴权与 HTTP 语义。

把这三样塞进 `obs/` 会让最底层反过来依赖上层。现在 `obs` 只吃 `Event` 值对象，
任何层都能安全地往上发事件，而"谁在听"（落库 / 广播 / 打印）由装配处决定。

## 六、事件总线与实时通道

- 进程内 `EventBus`，**发布同步非阻塞**：发布者跑在刷课关键路径上，
  慢订阅者只能丢事件（有界队列丢最旧），不能拖住发布者；
- 订阅者异常只计数，不倒总线；
- app 日志另有**一条同步广播**（`obs/log_stream.py`）：structlog 处理器把每条日志推给有界
  队列订阅者（满了丢最旧并计数，写日志的一方永不等消费者），并保留最近 200 条供新连接
  回放尾部；`GET /api/stream/logs`（`system:logs`，`?min_level=&backlog=`）把它转成 SSE。
  两条流故意不合并：领域事件答"发生了什么业务动作"，app 日志答"服务端怎么想的"；
- 前端实时数据与日志**全部走 SSE**：源码里禁 `setInterval`/`setTimeout`
  （由 Biome 规则强制，实测生效），保活由服务端超时承担。

## 七、鉴权与权限

- 口令链路：前端 `sha256(password)` = C → 后端存 `Argon2id(C)`；
  **登录不掺 nonce**（服务端只有 Argon2id(C)，无法校验掺了 nonce 的 proof——
  这是数学事实），安全边界落在 TLS + HttpOnly Cookie + 限速上；
  nonce 只用于注册与改密这类低频高危动作。
- 会话是**不透明 token 的哈希**：库被读走也无法直接冒用登录态。
- 权限是**节点表**（`account:read:all` 这类），每个路由都挂 `require(...)`；
  `tests/api/test_permissions.py` 遍历路由表做元测试，漏挂直接失败。
- 数据可见性：管理员看全部，普通用户只看自己的；越权一律 **404 而不是 403**
  （403 等于确认资源存在）。

## 八、前端（web/）

Vite + React 19 + TS（strict）+ shadcn + SWR + ky；Biome 是唯一的 lint/format。
实时数据只用 SSE（`useSWRSubscription` + `EventSource`），零定时器。
生产由 API 同源托管（SPA fallback，`/api` 之外全部回 `index.html`）。

## 九、已知约束与取舍

| 约束 | 原因 | 升级路径 |
| --- | --- | --- |
| `workers=1` | 进程内事件总线 + SQLite 单写者 | 换 Postgres + 外部队列 |
| 写事务串行 | SQLite 写-写互斥 | 同上 |
| 事件可能丢弃 | 有界队列保护发布者 | 关键事件改为直接落库 |
| SSE 单进程广播 | 同上 | 用 Redis pub/sub |

## 十、门禁

一条命令：`bash scripts/ci.sh`（Windows 侧的等价备份是 `scripts/ci.ps1`，两者跑同一组门禁）

```
ruff check → ruff format → mypy(strict) → import-linter → 路由尺寸
→ pytest → biome → tsc + vite build
```

另有两条自查规则：

- `scripts/check_router_size.py`：routers 必须是包、单文件 ≤200 行、**handler ≤15 有效行**
  （它已经拦下 6 次"编排逻辑堆在路由里"）；
- `tests/api/test_permissions.py`：路由表遍历 + 扫描逻辑自检
  （**元测试自己坏了比它要防的 bug 更危险**）。
