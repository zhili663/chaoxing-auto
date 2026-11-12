# 验收报告（对照 `REFACTOR_PLAN.md` §12）

> 逐条核对，**如实标注未完成项**。带 ✅ 的有实测证据；带 ⚠️ 的写明缺口与补齐方式。
> 核对时间：P7 收尾阶段（`99df5b9`）。

## 已通过（附证据）

| # | 验收项 | 证据 |
| --- | --- | --- |
| 1 | `uv sync --frozen` + `cx doctor` 干净环境一次通过 | `uv sync --frozen` 无输出（锁文件与 pyproject 一致）；`cx doctor` 9 项全 PASS：python / package / lock / config / exposure / secret_key / data_dir / database / frontend。**`--full` 的四项端到端语义已全部接上**（原先固定标 SKIP）：容器装配（订阅者 3 个）、`/api/health` 200、SPA 托管 200、`/api/events` 401（端点与鉴权都在位）、**真实账号登录态抽检**（库里没账号时标 SKIP 并给出下一步命令；有账号时**真的打一次学习通**校验会话）。走 ASGI 直连，**不占端口**，所以它在"服务没起"时同样能跑 |
| 14 | CLI 无越界子命令 | `tests/test_cli_surface.py`：顶层命令白名单 + 任何层级禁 `test-*`/`smoke-*`/`check-*`/`diag-*`/`e2e-*`/`preview-*`。网页与端到端检查确实由 `cx doctor --full` 承担（见第 1 条） |
| 2 | `pytest` 全绿，覆盖 client / storage / core / api 四层 | **394 项全绿**（2026-10-11 复核）；四层目录齐全（`tests/{client,storage,core,api}`），另有 `tests/test_cli_surface.py`、`tests/test_no_hardcode.py`、`tests/test_cli_dev.py` |
| 3 | `ruff check` / `mypy` / `lint-imports` 零问题 | `bash scripts/ci.sh` 全绿（含 mypy `strict`、import-linter 4 契约 KEPT；`ci.ps1` 是 Windows 侧的等价备份） |
| 6 | 权限元测试通过；首个用户自动超管 | `tests/api/test_permissions.py` 遍历路由表（含"扫描逻辑自检"）；`test_first_user_becomes_superuser` |
| 7 | 零硬编码审计通过 | `tests/test_no_hardcode.py`（AST 扫环境变量访问 + ID 扫描）；偏好白名单只允许业务参数 |
| 8 | "刷什么"全链路网页可 CRUD | 建账号 → 扫描课程 → 勾选 → 扫描任务点 → 运行，全部有对应路由与页面，全程不改代码与配置文件 |
| 9 | 协议覆盖清单全绿 | `cx dev probe coverage`：端点 20 个（VERIFIED 11）· 已有样本 4 个 · **未建模调用 0 条** |
| 10 | 测试用例可追溯到真实现象 / 抓包；无凑覆盖率用例；无覆盖率门禁 | 每个测试模块的 docstring 都写明来源（如"legacy 实测：缺 jtoken 返回 200 + status=false"、"抓包确认"）；`pyproject.toml` 里**没有** `--cov` 与覆盖率阈值 |
| 11 | 数据可见性正确 | `tests/api/test_accounts.py`：管理员看全部、普通用户看不到别人的、越权返回 **404 而非 403**、普通用户不指定账号看统计被拒 |
| 13 | 路由规范三项检查通过 | `scripts/check_router_size.py`：routers 全是包、单文件 ≤200 行、handler ≤15 有效行（15 个文件通过） |
| 15 | `pyproject.toml` 无手写依赖；linter 全绿 | 依赖段由 `uv add` 生成；`cx doctor` 的 lock 检查 PASS；ruff + biome 全绿 |
| 5 | 前端五页面可用、SSE 实时刷新与**断线重连**验证通过、定时器禁令零违规 | **已实机验证**（`scripts/web_check.sh`，orca-cli 驱动；早期版本用的是 `scripts/browser_check.py`）：跑通五页面；**断线重连**用"停服务 → 重启 → 看客户端是否自己接回来"验证 —— 页面探针 `open=2 / error=1`（重连成功），服务端日志 `SSE 已连接` 2 → 4（两条连接各自重连）。定时器禁令实测生效 + 源码零调用 | — | 已完成 |
| 18 | 内存复核 < 260 MB | **已实测**（`uv run python scripts/memory_check.py`）：空载 **101.8 MB** → 60 个并发请求后 **104.6 MB**（60/60 成功应答），余量 155 MB。读数**双源对照**（Win32 API 与系统工具完全一致）—— 这一点很关键：单源读数曾给出 5.6 MB 的"漂亮"假结果（量到了启动器而不是服务进程），现在按**端口反查**定位真正的服务进程 |
| 9 | 协议覆盖对账可回归（原先的「未建模 0 条」是空对账） | **已补齐**：脱敏后的真实录制入库（`tests/fixtures/recorded/course_session_urls.json`，**46 条请求**），`tests/client/test_coverage.py::test_recorded_session_is_fully_accounted_for` 钉住"未建模必须为空"；范围外调用（主机 / 后缀 / 前缀）与能力级缺口都**带原因**渲染进 `docs/PROTOCOL_COVERAGE.md`（32 端点 / VERIFIED 13）。忽略规则不许吃掉真接口也有守卫（`test_registered_endpoints_are_not_swallowed_by_ignore_rules`） | — | 已完成 |
| 12 | 三档断点响应式验证（375 / 768 / 1280） | **已实机验证**（`bash scripts/web_check.sh`，orca-cli 驱动）：三档视口各截一图，机器断言通过 —— 视口宽度实测生效（375 / 768 / 1280，脚本断言 `window.innerWidth`）、无横向滚动、标题正确、无页面错误。手机档导航转为顶部横向、工作台三栏**堆叠成单列**；桌面档为侧栏 + 4 列 KPI | — （截图在 `.browser-check/`，需人眼复核细节；脚本每次重新生成） | 已完成 |

## 未完成（缺口与补齐方式）

| # | 验收项 | 现状 | 缺口 | 补齐方式 |
| --- | --- | --- | --- | --- |
| 4 | `cx study run` 真实账号跑通视频 + 测验 + 文档，四类记录可查 | **测验 ✅ 完整达成 · 四类记录 ✅ 完整达成 · 视频心跳机制 ✅ 已打通并实测 200**。测验：**两次真实交卷**（第一章 95 分、第四章 84 分，服务端回执）。四类记录：`task_events` 17+ / `quiz_attempts` 2 / `audit_logs` 8 / app 日志。**沿途修掉 13 个只有真实环境才暴露的缺陷**，其中最后一个是心跳链路的根因：**心跳前必须先打开播放器页** —— 同一份 `/ananas/status` 在播放器页被访问前签发 `dtoken=8d7798fe…`（心跳必 403），访问后签发 `dtoken=9eb1bcae…`（心跳 **200**，与真实样本逐字一致）。**2026-10-11 实测探测**（账号 1 / 任务点 1「1.1.1(2).mp4」）：前 `dtoken=ea15fffa…`、后 `dtoken=ea15fffa…` → 结论「未激活」+ 适用性说明 + 建议动作，退出码 0 | **剩下的不是代码问题，而是一次人工动作**：在**从未被播放过**的视频上，打开播放器页后 dtoken 不变、心跳仍 403（账号 2 的「诗话青春——绪论」30 秒片，实测如此）；在**曾被真实播放过**的视频上，打开播放器页 dtoken 立刻可用（账号 1，实测 200）。即 dtoken 的"资格"由一次真实播放激活 | 在浏览器里把任意一个未播放的视频**播几秒**（30 秒那个就够），之后同一套代码即可自动上报。**判据已命令行化**：`uv run cx dev probe heartbeat --account N --point M` 会打印两个 dtoken 并给出「已激活 / 未激活」结论。全过程记录见 [`MOOC_ANS_403.md`](MOOC_ANS_403.md) |
| 16 | 部署 SOP ≤6 条命令；`deploy/` 脚本已 uv 化 | **已补齐**：`deploy/README.md` 的 SOP 恰好 6 条；`install.sh` 重写（装 uv → `uv sync --frozen --no-dev` → `cx db upgrade` → `cx doctor --fix` → systemd 直接跑 `cx serve --host 127.0.0.1 --port N`）；`nginx.conf` 与 `openresty-site.conf` 都加了 SSE 专用 location（`proxy_buffering off` + `proxy_read_timeout 24h`），并删掉指向已删除文件的注释 | **已补**：用 Git Bash 跑通 `bash -n`（install.sh 与 cert-renew-hook.sh 均通过）；shellcheck 未安装（语法层面已被 `bash -n` 覆盖） | — |
| 17 | `ARCHITECTURE.md` + `README.md` 与实现一致 | **已完成**：`docs/ARCHITECTURE.md` 已写；`README.md` 已重写并对齐实现 | — | （过程记录）README 原本列了 `cx account` / `cx user` / `cx group` 三个**尚未实现**的命令，已改成只列实际存在的（并注明未实现项）+ 加一句"可用 `cx --help` 复核" |
| 5（运行形态） | 开发形态（`cx serve` + `pnpm dev`）实跑 | **已实测（2026-10-11，orca-cli）**：API 起在 `127.0.0.1:8000`、Vite 起在 `127.0.0.1:5174`（注意 Vite 默认只绑 `localhost`/IPv6，脚本化时用 `--host 127.0.0.1`）；`curl http://127.0.0.1:5174/api/health` 与直连 API 的响应**逐字相同**（Vite 代理生效 → 浏览器里只有一个源）；orca 打开 dev 源：登录表单存在、页面内 `fetch('/api/health')` 返回 **200**、`errors` 为空；截图在 `.browser-check/dev-mode.png` | — | 已完成 |
| 1（补充） | `cx doctor` 的 `frontend` 项在两种形态下的期望值 | **没有 `web/dist` 时是 `SKIP`（开发形态的期望值，不是故障）**，提示里直接给了 `cd web && pnpm build`；构建过之后是 `PASS`。本机 dist 存在，故当前为 `PASS` | — | 已完成 |
| 19 | `legacy` 分支冻结打 tag，`v2.0.0` 可发布 | `legacy` 分支已冻结（`4f51bb7`）；`v2.0.0` tag 已打（`aaa359e`） | tag 打在合入 `master` 之前的验收提交上；`v2` 分支本身停在 `7f05425`（发布后的修复直接落在 `master`） | 分支整理归「仓库卫生」计划 |

## 附：计划里的 CLI 清单 vs 实际实现

`REFACTOR_PLAN.md` §5.9 列的是**目标状态**。实现过程中我优先做了网页路径（那里是用户真正要用的地方），
因此有几个 CLI 域没落地。**如实列出**，不假装计划全实现了：

| 计划中的命令 | 现状 | 说明 |
| --- | --- | --- |
| `cx serve` / `cx doctor` / `cx db {…}` | ✅ 已实现 | 见 README 命令表 |
| `cx auth {login,logout,refresh,stats}` | ✅ 已实现 | — |
| `cx course {list,scan,select}` | ✅ 已实现（多一个 `points`） | `points` 是扫描任务点，计划里漏了但必需 |
| `cx study {run,status}` | ✅ 已实现 | — |
| `cx study {pause,reset,logs}` | ❌ 未实现 | 暂停/重置需要刷课编排支持中断点；日志可先用 `journalctl` / `cx doctor --raw` |
| `cx auth import-cookie` | ❌ 未实现 | 读浏览器 Cookie 属于"绕过正常登录"的偷懒路径，legacy 里那个脚本已按此理由删除（见 LEGACY_TRIAGE） |
| `cx account {list,add,enable,disable,remove}` | ✅ 已实现（本轮补上） | 分工：`cx auth` 管登录态、`cx account` 管账号实体 —— 课程与任务点挂在实体下（外键），只登录不建实体时扫课会失败 |
| `cx user {list,create,grant,revoke,passwd}` | ✅ 已实现（本轮补上） | 口令口径与网页一致（先 sha256，后端存 Argon2id）；`passwd` 会**撤销该用户全部会话**（旧会话不该继续有效） |
| `cx group {list,create,add,remove,delete}` | ✅ 已实现（本轮补上） | 组是**视图与批量入口**：加入组**不改变账号归属**（可见性仍由账号自己的属主决定，有专门测试钉住这条安全底线）；加成员时逐个校验账号可见性，避免组变成越权通道 |
| `cx settings {show,set,unset}` | ✅ 已实现（计划外补充） | 三层配置的第三层需要它 |
| `cx stats` | ✅ 已实现（计划外补充） | 不出命令行看进度也得有 |

**结论**：CLI 的域已与计划对齐（`group` 本轮补上）。仍未做的两项都有明确理由：
`study {pause,reset,logs}` 属增强（暂停需要刷课编排支持中断点），
`auth import-cookie` 有意不做（读浏览器 Cookie 是"绕过正常登录"的懒路，
legacy 那个脚本已按同样理由删除，见 LEGACY_TRIAGE）。



## 一句话结论

**重构本身已经完成**：84 个提交、**394 项测试**、4 条分层契约、路由规范、零硬编码、定时器禁令
全部机器强制；`core` / `api` / `cli` / `utils` 分层落地，依赖方向由 import-linter 守住；
前端五页面 + 工作台四区 + 三档响应式（2026-10-11 起改用 **orca-cli** 复验）；
SSE 断线重连与内存预算（101.8 → 104.6 MB）均已实测；部署脚本 uv 化、SOP 六条命令。

**协议覆盖不再是"空对账"**：`docs/PROTOCOL_COVERAGE.md` 由
`cx dev probe coverage --recorded tests/fixtures/recorded/course_session_urls.json --write` 生成，
素材是脱敏后的真实抓包（46 条请求），守卫测试钉住"未建模必须为空"。
文档数字第一次与注册表一致：**32 端点 / VERIFIED 13**。

**接上真实账号后又修掉一批只有真实环境才会暴露的缺陷**（登录表单被当 query、`cx auth`
存储不一致、硬编码 GBK、卡片缺 `stuenc`、`enc` 取不到、卡片其实是 HTML、空章节误报、
`workid` 用成了 `mid`、解析路径优先级、`defaults.ktoken` 为空、**心跳前必须先打开播放器页**）。
这类缺陷 mock 测试一个都发现不了 —— 因为 fixture 是我们自己编的。

**一项待人工动作**（成因已查清，写在 [`MOOC_ANS_403.md`](MOOC_ANS_403.md) §五）：
**视频心跳的"资格"需要一次真实播放激活** —— 从未被播放过的视频，打开播放器页也不会换发可用的
`dtoken`（心跳仍 403）。判据已经命令行化：`cx dev probe heartbeat --account N --point M`
打印两个 dtoken 并给出结论；人工播几秒后重跑即可。

**测验链路已完整跑通**（两次真实交卷 95 / 84 分，服务端回执）；四类记录
（`task_events` / `quiz_attempts` / `audit_logs` / app 日志）都有实测数据。
扫课、选课、扫任务点、题库与统计、用户与权限、CLI 与网页全线可用。

---

## 2026-10-11 第二轮：UX 重构与数据可用性收口

**用户反馈**：KPI 全 0/null、SSE 日志不可用、账号与课程同页、扁平化表单。
逐条定位与处置见下表。

| 症状 | 根因（实测） | 处置与证据 |
| --- | --- | --- |
| KPI 全 0 / null | `/stats/kpi`、`/stats/daily`、`/stats/overview` 要求权限节点 `study:record:read`，而该节点**在 `permissions` 表里不存在**（角色模板后来加了它，存量库没有迁移）→ 对**所有人** 403；前端 `useSWR` 拿到 403 后渲染成 0/null | `UserService.sync_catalog()` 启动时幂等自愈（补节点 + 补内置角色授权，只加不删）；`admin` 模板改为**全量目录**（否则管理员连 `/groups` 都 403）。真库启动日志：`新增节点 20 个、角色 2 个、授权 46 条`；副本库实测 `/stats/kpi` **403 → 200**，返回 `169 / 71 / 14 / 12` |
| SSE 日志不可用 | 前端日志面板只吃领域事件；app 日志只有 REST `/api/logs` 且**前端从未调用** | 新增 `GET /api/stream/logs`（SSE，`system:logs` 权限，`min_level` 过滤 + `backlog` 回放），仪表盘加日志面板（按帧合并提交、按内容去重、可清空）。实测：浏览器里真实日志行可见（如 `23:08:28 [access] …`） |
| 账号与课程同页 | 旧工作台把"账号列表 / 新建账号"与"课程 / 任务点 / 控制台"塞在一个三栏页 | 拆成 `/accounts`（账号台账：列表 + 二级 Dialog 新建 + Sheet 详情 + 分组切换）与 `/courses`（账号切换 Combobox + 课程/任务点 + 计划 Dialog）；`web/src/pages/workbench/` 整目录删除 |
| 扁平化表单 | 用户页 / 设置页把配置铺成一张长表单 | 全部改二级 surface：添加用户 → Dialog、计划 → Dialog、账号 → Dialog、详情 → Sheet；设置按"模型 / 提示词 / 界面 / 高级"分组，控件按语义选（Slider + 数字、Switch、masked 输入 + 替换） |

**顺带修掉三个真缺陷**：

1. `DELETE /groups/{id}/members` 需要 body，而前端 `apiDelete` 不带 body → 补路径式
   `DELETE /groups/{id}/members/{account_id}`（含 API 用例）；
2. **React #185**（`Maximum update depth exceeded`）：日志 SSE 一次回放 80 条，逐条 `setState`
   在同一轮里嵌套 → 仪表盘在 768 / 1280 档直接报错。修法是**按帧合并再提交**（微任务而非 rAF ——
   后台标签页里 rAF 不触发，面板会一直是空的）；实测三档视口 0 错误且日志正常出线；
3. `ProcessorFormatter` 会把 `foreign_pre_chain` **给每个 handler 各跑一遍** → 同一条日志在
   SSE 流里出现两次。日志广播改为**独立 Handler**（每个 `LogRecord` 只 emit 一次），并有回归用例钉住。

**门禁与实机验证**：`bash scripts/ci.sh` 全绿；新增 `scripts/ux_check.sh` —— 逐页断言关键文案
（含"日志面板不得为空 / 不得断开"这类**否定断言**）+ 7 张截图；`scripts/web_check.sh` 三档视口全过。
脚本自身也修了两个坑：登录按钮 `disabled` 时的点击竞态、以及 `grep` 漏写 `-o` 导致 ref 提取为空。

### 2026-10-12 复核：窄屏交互 + 空库形态

| 复核项 | 方法 | 结果 |
| --- | --- | --- |
| 窄屏（375）日志抽屉 | 点检脚本新增「窄屏交互」段：点开抽屉后断言出现**真实日志行**且不出现空态文案 | ✅ 抽屉里能看到 `[access] …` |
| 窄屏账号页是卡片列表 | 断言桌面 `<table>` 在 375 下 `getClientRects().length === 0`（用 `getComputedStyle` 会误判：祖先 `display:none` 时子元素自身的 display 仍是 `table`） | ✅ 表格不占版面 |
| 窄屏「添加用户」Dialog | 断言 375 下点击后出现**可见**且含「用户名」字段的对话框（关闭态 Dialog 可能仍在 DOM 里，必须按可见性判定） | ✅ 打开且字段齐全。**焦点落位**在无头标签下不稳定（`activeElement` 停在 `BODY`），同一表达式在手动/前台标签里稳定为 `true` —— 脚本里降级为参考值，并如实记录这个限制 |
| 有数据形态 | 完整点检 7 页 + 窄屏段（真数据副本，`points_total=169 / done=71`） | ✅ 全绿、0 页面错误（截图 `.browser-check/ux-*.png`） |
| 无数据形态 | 空库（`cx db upgrade` + 首个用户）上跑仪表盘 / 账号页 | ✅ KPI 显示**真 0**（`0个 / 0分钟 / 0次`，不是 `—` 占位）、热力图给「这段时间还没有完成记录 —— 先在课程页跑一次扫描 / 刷课」、账号页给「还没有超星账号 · 先添加一个账号（手机号 / 学号）并登录…」、0 页面错误（截图 `empty-dashboard.png` / `empty-accounts.png`） |

**点检脚本自身的三个坑（都会造成假结论，已修）**：

1. **`goto` 会重置视口** —— 窄屏断言其实跑在默认宽度（1180）上，"日志抽屉通过"是假阳性。
   `visit()` 现在每次导航**之后**再设一次视口，并先断言 `window.innerWidth` 落在 300–500；
2. **点早了等于没点** —— SPA 未水合时原生点击没有 React 监听；而盲目重试会把已打开的对话框再关掉。
   点击并入重试循环，用 `window.__uxAddUserClicked` 标记保证只点一次；
3. **残留标签页会拖垮 orca 运行时**（实测 `goto` 直接返回 `runtime_unavailable`）——
   两个脚本都加 `trap cleanup EXIT`，无论成败关掉自己开的标签页；导航失败重试一次。

### 2026-10-12 backlog 收口（四项全做）

| 项 | 交付 | 证据 |
| --- | --- | --- |
| 刷题记录面 | `GET /api/quiz/attempts`（只读服务 + 路由 + 13 用例）+ 刷题页拆包与「交卷记录」区（表格 / 窄屏列表 / 详情 Dialog / 三态） | 副本库实测：`account_label=187****8327`（脱敏）、`course_title=国家安全教育`（join 出来的真课程名）、按 `created_at DESC`、字段集与模型**逐字段**一致；越权 404、非管理员不指定账号 400；浏览器点检断言 `交卷记录` |
| 前端类型生成 | `scripts/gen_api_types.sh`（应用对象 → OpenAPI → `api-types.ts`）+ `hooks.ts` 去掉手写 DTO + CI 漂移检查 | 生成物含 57 条路径；适配了 3 处**可选性差异**（`last_check_at` / `scanned_at` / `warnings` 在生成类型里可选）；CI 重生成后 `git diff --exit-code` 通过（漂移即红） |
| 刷课中断/重置 | `study_runs` 控制表（迁移 `a61dcdcb05af`）+ 运行器检查点（单元边界 / 每次心跳）+ CLI `pause/resume/reset/logs` + API 三端点 + 网页「刷课控制」（5 态徽章 + 暂停/继续） | 真库副本上：迁移 `805e0a0fa35b -> a61dcdcb05af` 成功；CLI 实测 `pause→pause_requested`、`resume→running（撤销未生效的暂停）`、`reset→84 个任务点打回 queued`、`logs` 最新在前；浏览器实测点「暂停」→ 徽章变「正在暂停…」+ 提示「运行器会在下一个检查点生效」，点「继续」→ 回「运行中」；新增 21 条用例（含"暂停中一个单元都不开""心跳中断但进度不丢"） |
| 检索层 | `ai/search.py`（DDG HTML + 假实现）+ 提示词片段渲染 + 引擎接线（`websearch_enabled` 门控） | 离线用例（respx）覆盖命中 / 超时 / 空结果 / 异常四支，全部**不阻断作答**；开关为 false 时零请求 |

**未做/未验证（如实记录）**：

1. **真账号实测已做（2026-10-12）**：两个真实账号、两门课上跑通 会话校验 → 扫课（6 门）→ 扫任务点（礼仪 7 / 心理健康 71）→
   真刷 19 个待处理点 → **跨进程暂停/继续**（`pause_requested` → 检查点收尾「还有 1 个播放单元未执行」→ `resume` 复位），
   已验证进度保留、下一次 run 从断点继续。实测暴露 **1 个新缺陷**（P-2 文档任务点缺 jtoken：原始卡片里
   就没有签发，需按真实流程抓包再改）、1 个既有问题的新证据（P-3 测验 403）、2 条观察（P-4 确定性失败仍被重试、
   P-5 部分点标题为空）。**仍未做**：修复 P-2 之后的复测（属下一轮工作）。
2. **检索层的真实端点未实测** —— 本机 `html.duckduckgo.com` 不可达（`httpx.ConnectTimeout`），
   选择器只依据公开 HTML 结构 + mock 用例验证。联网环境复核一次即可闭环。
3. 真库（`data/app.db`）需要跑一次 `cx db upgrade` 才有 `study_runs` 表；迁移已在**真库副本**上验证过。

### 2026-10-12 夜：真机实测暴露问题的根因修复（P-2 / P-4 / P-5 收口）

| 问题 | 根因（判据） | 处置 | 真机验证 |
| --- | --- | --- | --- |
| P-2 文档点 4/4「缺 jtoken」 | 同一章节响应稳定带 jtoken（8599 字符、6/6 次），但 `extract_cards_payload` 产物没有 —— 它**优先用 HTML 卡**（带类型信号、无 jtoken），只在内嵌脚本兜底时才看 `mArg`（有 jtoken）；两份载荷各有缺失，取其一必丢一个 | HTML 版为基底，按身份键（`jobId`/`property.id`/`_jobid`/`mid`）合并脚本版独有字段；`chapter_cards_mooc` 的"待校准"注释同时收口 | 重扫后 4 个文档点全部 `jtoken=有(32)`（此前全空） |
| P-4 失败点被反复重试 | `_load_points` 把 `status='failed'` **无条件**选进来 | `RETRY_LIMIT=2`（失败只再试一次；要重试走 `cx study reset`） | `run --limit 3` 只挑了 retry<2 的 3 个点，**14 个到上限的点没被碰**（retry 仍 3–4） |
| P-5 任务点标题为空 | 卡片把显示名放在 `property.title`，`Attachment.name` 只认 `name`/`filename`，而落库取 `attachment.name` | 别名收下 `title` + 用例 | 重扫后三个空标题就位：`第一章 章节测验` / `文学与人生` / `问卷调查` |

**仍未闭环（都卡在"浏览器里没有可用的 mooc1 会话"）**：

1. ~~**阅读卡的完成流程**~~ —— **已闭环（2026-10-12 深夜，真机抓包确认）**：
   浏览器抓包确认这类卡走 **`GET /mooc-ans/coursedata/readjobv2/show`**（带卡片附件的 `enc`）而不是
   `/ananas/job/{document,readv2}`；已实现端点 + `DocumentApi.finish_read_v2` + 按「卡片带 enc」分派，
   真机验收：礼仪课 5 个文档点全部 `done`，且 `sync_progress` 报 `marked=0`（本地与平台完全一致）。
2. **测验 403**：直连题目页实测仍返回 3004 错误页（820 字符）→ 证实"必须先换票"，
   而换票本身 403 → 两条路都被同一道墙挡住，需要浏览器的真实换票请求做逐字段对照。
3. **F5 结论（已得，无需浏览器）**：章节 `757083103` 在课程树里**正常可点、无锁定标记**，
   但"先访问章节页再取卡片"后仍 3004 → 判定为**课程侧限制**，保持现状（跳过 + 警告）并记录于此。



## 追加（2026-10-12 夜）：测验链路闭环，答题来源待配

第 4 条的**测验部分**今天又推进了一步，状态更新为：

- **换票 / 取题：已完整闭环**（真机 15/15）。同一批测验点此前是 `api/work` **403** 或
  `workHandle` 弹登录页，现在**全部**到达题目页并解析出题目（错误信息统一变成
  「一题都没答出来（共 N 题）」）。
  - 根因一：换票缺课程级参数 `utEnc`（现由 `CourseService._fetch_utenc` 取回并写进 payload）；
  - 根因二（真正的拦路虎）：**中间跳的 Set-Cookie 没被带回下一跳** ——
    cookie 由我们放进请求头发送，httpx 自动跟随只在第一跳前取一次 jar，
    而学习通把 `k8s` 设在 302 上；改为 `ChaoxingTransport._follow()` 逐跳"先吸收再请求"。
- **答题来源：未配置**。本地题库只覆盖此前见过的题；`.env` / `settings_overrides` 里
  没有 `AI__*`（`build_provider()` 返回 `None`），因此 15 个测验都停在「缺答案」。
  答题引擎在缺 provider 时**在提交之前**就抛错 —— 不会交白卷，对账号零风险。
- 其余任务点两门课全绿：video 76 done / document 5 done / quiz 2 done（题库命中）。

逐点状态见下方追加记录。

## 追加（2026-10-13 凌晨）：测验**真实交卷成功**

配置 `AI__*`（DeepSeek 兼容端点）后，`cx study run` 首次把测验跑通到**服务端回执**：

```
{"msg":"success!","stuStatus":5,"backUrl":"…workId=55611821&answerId=55414411&enc=81ba8d5e…"}
quiz_attempts: question_count=15 / answered_count=15 / submitted=1 / reply='success!'
```

同一条流程对 #18 连做两次都成功 —— 机制本身成立。此前卡住的两处新根因（都只有真机才暴露）：

1. **装配缺陷**：容器建了带 provider 的 `AnswerEngine`，但 `StudyService` 没拿到它，
   内部 fallback 成"无 provider"的引擎 → 每题判"不可用"（表现为「一题都没答出来」且 6 秒返回）。
2. **票据时效**：payload 里存的是扫描时的卡片 `enc`，而它**几分钟就轮换** ——
   扫描后立刻跑能成功，隔几分钟换票可能仍过、但**提交会被服务端拒**（「作业提交失败！」）。
   修复：执行前**就地刷新票据**（`cac7c5a`）。

剩余 13 个测验的精确分类（逐点取证）：
**6 个含连线题/填空题**（需按题型自己的作答格式提交，属功能缺口）、
**7 个全标准题型却仍被服务端拒**（已排除会话/票据/URL 形态/Referer/UA/已交卷/答案数量；
下一步需在浏览器里看一眼「提交」按钮是否可用，以区分平台侧前置条件与缺字段）。

## 追加（2026-10-13）：测验提交协议对齐，测验点全部完成

第 4 条**完整达成**：账号 1 的两门课里 **17 个测验点全部 `done`**（工具自动交卷 13 个 + 用户手动 2 个 + 此前 2 个），
服务端回执为 `{"msg":"success!"}`；视频 76 个 / 文档 5 个同样全绿。

四条关键协议事实（都由用户在 DevTools 导出的**成功**提交 cURL 证明，非推断）：
**① 提交必须带 XHR 头**（`X-Requested-With` 等，缺了 body 再对也回「作业提交失败！」）；
**② 判断题值是 `true`/`false`**；**③ 连线题值是 `[{"name","content"}]` JSON**；
**④ 已交卷的测验换票落到批阅页 `selectWorkQuestionYiPiYue`，要判 `already_done`**。
填空题格式仍无成功样本（`#176` 是带着一道留空的填空题提交成功的），已在代码里明确「不许原样透传」。

## 追加（2026-10-16）：账号 2 · 换票 403 定论、提交目标纠正、人工验证码阻塞

换到**账号 2**（`268133691:157322100` 创新创业基础）后，同一套链路再次暴露问题，逐条定论：

**① 换票 `api/work` 的 403 = `ktoken` 是旧的**（A/B 单变量实验）：
同 URL、同会话，仅替换 `ktoken` —— 浏览器现用的 `ffc558eb…` → **200**；
库里扫描时存的 `0e637043…` → **403**。与此前怀疑的 TLS / Referer / XHR 头 / 会话上下文**全都无关**；
`CourseService.refresh_quiz_tokens()` 取卡片 `defaults.ktoken` 本就正确，实测工具已能自行越过换票。

**② 交卷目标必须用页面表单的 `action`**：`form1` 的 action 与"自拼查询串"根本不是一套
（`_classId`/`courseid`/`workid`/`testmooc2` vs `classId`/`courseId`/`workId`/`mooc2`，
且 action 带 **`token=<隐藏域 enc_work>`** 与 `totalQuestionNum`）。
**字段集逐一对齐（29/29，无缺无多）仍被拒** —— 键名与取值必须照 action 原样 + `version=1`。

**③ 提交前有校验码预检**：`GET /mooc-ans/work/verify/code` 成功时回 `{"enc":…,"status":true}`，
该 `enc` 即 body 的 `workTimesEnc`；缺这一步一律回「作业提交失败！」。
注册表里原先把 `/verifyCode/stuWork`（**验证码图片**入口）当成了它，已拆成两个端点。

**④ 仍被拒的一环 = 服务端要求人工验证码**：同一接口在**同账号同参数、不同会话**下会在这两句之间摆动
（`{"enc":…,"status":true}` 与 `{"msg":"验证码不正确，请重新输入","status":false}`），
因此不能靠字符串匹配判断，已在 `QuizApi.submit()` 里加**主动探针**。
`ddddocr` 默认模型 / 中文 beta / 二值化+去噪+放大 三路 OCR 实测**均不可用**（乱码、长度 3–6 不等）。
落地「验证码接力」：`fetch_verify_captcha()` 取图 → 服务端落 `data/captcha/quiz-<workid>-<ts>.jpg` →
抛 `HumanRequired`（含路径）→ 人工读码后 `StudyService.run_quiz(account_id, point_id, input_code=…)` 交卷。

**影响面**：账号 2 课程 24 的 8 个测验处于「要求人工验证码」态（学习通侧 `remote_done=6 / marked=0`，
即确实未完成）；其余任务点（video 144 done / document / 其余排队点）不受影响。

**回归**：全套 `pytest` 在最新源码上通过，仅 2 项 `test_migrations` 失败 ——
它们内部调 `uv run alembic`，在隔离沙箱里因依赖同步超时，属环境而非代码。

### 2026-10-16 晚：**8 个被拒测验全部交卷成功**，真根因是「答案形状 vs 页面题型」

同课 38 个测验一直成功、8 个总是回「作业提交失败！」。最终用"只交形状确定的题"做对照：

```
只发 2 个答案 → EnvelopeFailure: 无效的参数：code-2！   ← 参数校验通过，卡在业务规则
把 answertype=3（判断）那题的 "ABCD" 换成 "true" → success!   ← 整卷立刻通过
```

⇒ **真因**：明文题与题目页**按索引合并**后题型错位；页面 `answertype{id}=3`（判断）的位置
发出了字母答案，服务端**整卷拒绝**并只回笼统的「作业提交失败！」（不是只扣那题分）。

**修复**：`sanitize_answers_by_type()`（`client/api/quiz.py`）以页面的 `answertype{id}` 为准校验形状，
不符则**留空该题**（服务端接受留空，拒绝错形）。真机结果：

```
1436 already_done · 1442..1472 done   → 课程 24 的 46 个测验点全部 done
```

**遗留（提分项）**：索引合并仍会错位，本次有 8 处答案被形状校验丢弃；
`#1436` 得分 37.5。下一步按「answertype + 选项数」对齐明文题与题目页，而不是按索引 ——
提交链路已通，剩下的是分数质量。

### 2026-10-16 深夜：验证码接力接到 UI（任务点 `need_human` → 弹框输入）

- **后端**：`GET /api/study/points/{point_id}/captcha` 取该点最新的验证码图片（`data/captcha/quiz-<workid>-<ts>.jpg`）；
  `POST` 同一路径 `{input_code}` 带码重试 `StudyService.run_quiz(...)`。
  权限沿用 `study:record:read` / `study:plan:run`，写入走 `require_same_origin`。
- **服务层**：`run_quiz` 现在**持久化状态**（与 `_dispatch` 一致：`HumanRequired` → `need_human`，
  `done`/`already_done` → `done`），此前只返回字符串不落库。
- **前端**：任务点表格对 `need_human` 的行显示「验证码」按钮，弹框内展示图片 + 输入框 +
  「提交并继续」，成功后刷新任务点列表（窄屏同样有入口）。
- **验证**：前端 `tsc -b && vite build` 通过；线上 bundle 内含新文案；`create_app().openapi()`
  可见该路由（58 条）；受影响测试 45/45 通过。
  **限制**：当前没有处于 `need_human` 的点（两账号测验均已交卷），无法点一遍真实流程 ——
  首次出现时再复核即可。

### 2026-11-07：参考实现对照 + 换票/交卷加固

**参考实现**（只取逻辑，不复制代码）：

- `Samueli924/chaoxing`：`knowledge/cards?num=0..6` 逐张扫描；换票/取题带重试；
  视频心跳按随机 30~90s 上报并在 403 时刷新 status/重登；提交后查成绩决定是否重做。
- `RainySY/chaoxing-xuexitong-autoflush`：章节 → 卡片 `num` 递增直到解析失败；
  每 60s 一次心跳、结束补一次；`isPassed` 的任务点直接跳过。

**本项目已具备**：账号级限速、卡片 `num` 扫描、明文题/题型对齐、答案形状校验、
验证码接力；这轮只补“失败后的恢复路径”，不改变已验证的提交形状。

**改动**：

1. `client/api/quiz.py`
   - 校验码预检按 XHR 形状发：`X-Requested-With` + 本页 `Referer`，不再给 GET 加表单 Content-Type；
   - 预检参数兼容 `courseid` / `classid` / `workid` / `answerId` 等大小写别名；
   - 非“验证码”类预检失败降级走旧路径，不再一律判人工；
   - 提交目标用 `urljoin` 保留表单 action 自己的路径；`version=1` 已存在时不重复追加。
2. `core/services/study.py::_open_quiz_page`
   - 换票 403 / 3004 后**重新扫卡刷新 `ktoken`/`enc`/`utEnc`** 再换一次，
     不重放同一组旧票据；仍失败才把原始错误抛给任务点。
3. `client/transport.py::_follow`
   - 302 逐跳跟随时改走 `_client_for(session)`，与首跳使用同一个代理出口；
     此前误用直连 client，代理池接入后会表现为“换票中途跳出代理”。
4. `api/routers/study/captcha.py` 补齐返回类型注解；`client/proxy.py` 仅格式化。

**验证**：

```
uv run pytest -q                  # 全部通过，110.42s
uv run ruff check .               # All checks passed
uv run ruff format --check .      # 263 files already formatted
uv run mypy src                   # Success: no issues in 167 source files
uv run lint-imports               # 5 kept, 0 broken
```

代理池本身**尚未接入**（等下一轮提供）；本轮只保证换票/登录的每一跳都会跟随
当前账号的出口，不会在 302 中间跳偷偷回到直连。

### 2026-11-07：proxy_pool sidecar 接入（B 方案）

**判断**：`jhao104/proxy_pool` 是 Flask + Redis/SSDB 的独立服务，不是可 import 的
Python 插件；本项目按 **sidecar** 接入，不合并它的依赖（其 Flask 2.1 /
Python 3.8–3.11 与本项目 Python 3.12 冲突）。

**实现**：

1. `client/proxy_source.py`：`ProxyPoolClient` 访问
   `GET /all/?type=https`、`GET /delete/?proxy=...`、`GET /count/`；
   把 API 可能带出的 `https://` 归一成 `http://`（支持 HTTPS CONNECT 的普通代理
   不等于 TLS-to-proxy）。
2. `client/proxy.py::ProxyPool.sync_remote()`：远端快照替换出口，**保留同 URL 的
   账号粘性、冷却、成功/失败计数**；远端删除的出口解绑。
3. `client/transport.py`：
   - 每 `CHAOXING__PROXY_POOL_REFRESH_S` 秒拉一次快照；API 失败保留旧池并在 30s 后重试；
   - 403 时本地冷却 + `ProxyPoolClient.delete()` 远端删除，并让下次请求立即刷新；
   - 302 逐跳仍走当前账号出口；`aclose()` 现在会关闭所有代理客户端和代理池客户端。
4. `cx proxy remote [--url] [--save]`：只读查看远端 HTTPS 出口，或写入
   `data/proxies.txt` 作离线兜底。
5. 配置：`CHAOXING__PROXY_POOL_API / _REFRESH_S / _TIMEOUT`；不配置则行为不变。
6. 文档：`docs/PROXY_POOL.md`。

**验证**：

- 新增 `tests/client/test_proxy_source.py`（MockTransport 验证 API 契约/归一化/删除）、
  `tests/client/test_proxy_pool.py` 的远端快照合并、`tests/client/test_transport.py`
  的周期刷新/403 删除/代理池故障保留旧池。
- 用本地假 `/all/`、`/count/` 服务跑通 `uv run cx proxy remote --url ...`，输出 2 条出口。
- 全量 `pytest` 通过（121.38s）；`ruff` / `mypy` / `import-linter` 通过。

**限制**：当前机器没有 Docker/Redis，未现场启动 proxy_pool；接口契约按源码与
MockTransport 验证。proxy_pool 的可用性校验目标是 qq.com/httpbin，不是学习通；
能否过 `mooc-ans` 仍以真实请求为准（403 即本地冷却并远端删除）。

### 2026-11-07：多账号并发刷课核查

**结论：已有，不需要重复实现。**

- 调度器：`core/scheduler.py::AccountScheduler` 用 `asyncio.Semaphore(max_concurrency)` +
  `asyncio.gather` 并发跑多个账号；单账号异常被收进 `SchedulerReport.errors`，
  其它账号继续。
- 入口：`cx study run --all` / `cx study run --accounts 1,2,3`；上限读
  `CHAOXING__MAX_ACCOUNT_CONCURRENCY`（默认 3）。
- 隔离保证：`SessionState` 按账号持有 cookie，transport 刻意不设共享 cookie jar；
  代理出口按账号粘性；SQLite 写事务由 `UnitOfWork` 写锁串行；控制信号按账号键。
- 回归：新增
  `tests/core/test_study_service.py::test_two_accounts_run_concurrently_without_cookie_crosstalk`，
  用两个真实 `SessionState` + 共享 transport 并发跑视频，断言两份报告都完成，
  且每个请求只带自己的 `_uid`（不串号）；既有 `tests/core/test_scheduler.py` 覆盖
  并发上限、失败隔离、结果汇总。

网页端 `/study/run` 仍是**单账号**触发；多账号并发的正式入口是 CLI/调度器。

### 2026-11-07：仪表盘「并发刷课」模块

- 后端 `StudyService.run_account` 现在写入 `study_sessions`（running → finished/aborted）；
  `StudyService.active_accounts()` 返回 running 会话 + 当前 running 任务点。
- 新端点 `GET /api/study/active`：`{"concurrency": N, "accounts": [...]}`；
  按 `account:read:all` 收敛范围（管理员全量，普通用户自己的账号）。
- 前端仪表盘新增「并发刷课」卡片：常开标识、并发上限、每个账号/任务/进度/开始时间；
  空态明确提示"并发模式已开启"。
- 刷新：SSE 事件到达立即重取；该卡片另以 3s `refreshInterval` 兜底
  （CLI 进程的 SSE 不进入 API 进程，活跃状态只能读库）。
- 测试：`tests/api/test_study_active.py`（空态/并发数）、
  `tests/core/test_study_service.py`（running 会话 + 当前任务点、结束后清理）；
  用 mock API 对构建产物做浏览器截图校验，确认两个账号、任务类型、常开徽章都渲染。

### 2026-11-07：按账号日志与刷题失败可观测性

- 新增 `obs/account_log.py::AccountLogWriter`：按账号写
  `data/logs/accounts/<account_id>.jsonl`，5MB 轮转到 `.1`；字段含事件、任务点、
  错误类型/消息、服务端原始 payload、traceback；cookie/token/password/session 等键脱敏。
- `StudyService.run_account` 记录 `run_started` / `run_finished` / `run_crashed`；
  `_dispatch` 记录 `task_started` / `task_finished` / `task_failed` /
  `task_need_human` / `task_crashed`。主日志只留一行摘要，完整细节在账号文件。
- 异常保留原始返回：`EnvelopeFailure.payload` 现在包含服务端 JSON 失败体；
  非 JSON 响应保留 `{"raw": ...}`；403 的 `RequestBlocked.payload` 保留状态码/URL/正文；
  `ErrorPageDetected.payload` 保留命中标记与正文片段。
- 新命令：`cx study logs --account N --raw [--limit N]` 直接查看账号 JSONL。
- 测试：`tests/test_account_log.py`（分账号/脱敏/读取）；
  `tests/core/test_study_service.py`（失败时 payload + traceback 落账号日志）；
  `tests/client/test_api_quiz.py`（提交失败异常携带原始 msg2/status）。

