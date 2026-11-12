# legacy/tools 分流决策（P7）

> 一次性报告，记录 `legacy/`（110 个 py 文件，其中 `tools/` 61 个脚本）的去向。
> 依据 `docs/REFACTOR_PLAN.md` §8.2：**工具脚本不做 1:1 迁移** ——
> 能归 CLI 的归 CLI，能归测试的归测试，一次性的排障脚本直接删除。
>
> 删除理由不是"它们没用过"，而是**它们记录的是当时的排障过程，不是可复用的能力**。
> 真正的产出（协议事实）已经沉淀到端点注册表的 `calibration` 与测试里。
> 需要考古时用 `git show legacy:<path>`（`legacy` 分支是冻结基线）。

## 汇总

| 去向 | 数量 | 说明 |
| --- | ---: | --- |
| 归入 `cx` CLI | 14 | 日常运维动作，已有对应子命令 |
| 归入 pytest | 11 | 变成断言而不是脚本 |
| 归入端点注册表 | 6 | 抓包得到的协议事实沉淀为 `calibration` + 备注 |
| 删除 | 27 | 一次性排障 / 调试 / 临时修补 |
| 无对应能力（暂缓） | 3 | 见文末"未迁移项" |

## 一、归入 `cx` CLI（14）

| 旧脚本 | 新命令 |
| --- | --- |
| `list_courses.py` | `cx course list --account N` |
| `scan_points.py` | `cx course points --account N --course KEY` |
| `setup_accounts.py` / `batch_import.py` | `cx account add` / `cx db import-legacy` |
| `disable_account.py` / `toggle_account.py` | `cx account enable/disable`（实体管理接口） |
| `probe_endpoints.py` | `cx dev probe endpoints` |
| `capture_heartbeat.py` / `capture_quiz.py` / `capture_submit.py` | `cx dev capture {browser,proxy,diff}` |
| `check_deploy.py` / `verify_config.py` | `cx doctor [--full] [--raw]` |
| `init_env.py` | `cx doctor --fix` |
| `login_smoke.py` | `cx auth login` |

设计约束（rev4）：`tools/` 里的脚本**不是**逐条变成子命令，而是按"后端本该有的运维动作"
重新归类，允许 `cx <域> <动作>` 两级嵌套；`test-*` / `smoke-*` / `check-*` / `diag-*` /
`preview-*` 这类命名在任何层级都被禁止（`tests/test_cli_surface.py` 守着）。

## 二、归入 pytest（11）

| 旧脚本 | 新的位置 |
| --- | --- |
| `selftest.py` | 各层单测（`tests/{client,storage,core,api}/`） |
| `smoke_api.py` / `live_test.py` | `tests/api/` 的端到端用例（TestClient + lifespan） |
| `test_db_lock.py` | `tests/storage/test_uow.py`（写串行与锁行为） |
| `test_document.py` / `test_login.py` / `test_qa.py` | `tests/client/test_api_document.py` / `test_login.py` / `test_parser_quiz.py` |
| `test_font_decrypt.py` | `tests/client/test_parser_quiz.py`（明文题渠道，绕字体反爬） |
| `check_style.py` | `ruff` + `biome`（CI 门禁） |
| `check_activity.py` / `check_snapshot.py` / `check_pending.py` | 统计与状态查询（`cx stats` / `cx study status`） |

原则：**用断言表达期望，而不是用脚本打印给人看**。脚本的输出需要人判断对错，
断言则每次运行都在替你判断。

## 三、归入端点注册表（6）

| 旧脚本 | 沉淀位置 |
| --- | --- |
| `probe_dtoken.py` | `endpoints/video.py` 的 `video_info`（含 dtoken 字段备注） |
| `probe_heartbeat.py` / `compare_heartbeat.py` | `endpoints/video.py` 的心跳端点 + `signature.py` 的 enc 算法 |
| `probe_quiz_api.py` / `probe_quiz_entry.py` / `probe_quiz_token.py` | `endpoints/quiz.py` 的换票 / 明文题 / 提交三件套 |
| `verify_enc.py` | `tests/client/test_signature.py`（用独立构造的 payload 验证算法） |
| `analyze_quiz_page.py` / `dump_quiz_page.py` | `tests/fixtures/quiz_page.html` + `parser/quiz.py` 的注释 |
| `fetch_player_js.py` / `probe_player.py` | `signature.py` 的 `VIDEO_PLAYER_REFERER`（版本号写错即 403，已记在注释里） |

这些脚本的价值是**当时发现的协议事实**。事实留下，脚本本身没有再跑的意义 ——
`cx dev probe endpoints` 会实时核对"注册表里写的东西还成立吗"。

## 四、删除（27）

一次性排障与临时修补，没有复用价值（保留它们只会让人误以为还有别的入口）：

- **诊断类（9）**：`diag_compare` `diag_failed` `diag_more` `diag_progress` `diag_quiz`
  `diag_time` `diag_unknown` `diagnose_cards` `diagnose_courses`、`diagnose_search`
- **排查类（4）**：`check_server_data` `check_deepscan` `diff_live_vs_capture` `replay_capture`
- **重置类（3）**：`reset_attempts` `reset_failed` `reset_running`（直接改库状态，
  危险且绕过业务规则；需要重置请用 `cx study` 与网页上的显式操作）
- **临时修补（3）**：`server_fix` `assist_submit` `reply_discussions`
- **页面/资源处理（3）**：`clean_illustrations` `preview_page` `cookie_from_browser`
  （后者读浏览器 Cookie，属于"绕过正常登录"的偷懒路径，且手机会话本就该走 `cx auth login`）
- **其他（5）**：`compare_heartbeat` `fetch_reference` `check_deepseek` `verify_enc`
  （前三个已被端点注册表与 `cx doctor` 取代，`verify_enc` 已变成单测）

## 五、未迁移项（3）

这三项在新架构里**暂时没有对应能力**，如实记录，不假装已经覆盖：

| 脚本 | 说明 | 后续 |
| --- | --- | --- |
| `reset_attempts.py` | 重置某次测验的作答记录 | 需要时以"显式、带审计"的接口重做，不恢复脚本 |
| `assist_submit.py` | 人工辅助提交（半自动） | 与 `HumanRequired` 事件 + 网页人工介入流程合并考虑 |
| `reply_discussions.py` | 讨论区回帖 | 属于新的任务点类型，按 `kind=discussion` 扩展（当前 `_dispatch` 会明确跳过） |

## 附：`legacy/` 其余部分

- `legacy/app/`（旧 FastAPI 应用，含 `static/index.html`）→ 前端已完全重写，**删除**；
- `legacy/core/`（旧协议层与调度）→ 能力已按 domain 重写到 `src/chaoxing/{client,core,storage}`，
  内存里"卡"住的协议细节（enc 算法、heartbeat 路径参数、`_jobid` 位置、错误页指纹）
  都有对应注释与测试，**删除**；
- `legacy/requirements*.txt` → 依赖改由 `uv` 管理（`pyproject.toml` + `uv.lock`），**删除**；
- `legacy/tools/` → 按本文档分流，**删除**。

删除前的核对（已执行）：

1. `src/`、`tests/`、`scripts/` 中**无** `import legacy`；
2. **无**任何代码引用 `legacy/` 路径；
3. `ci.ps1` 全绿（331 项 pytest + 前端 biome + 构建）—— 即"移除后一切照常"。

## 附：`data/` 里的研究材料（**保留**，不入库）

`data/` 在 `.gitignore` 里，所以下面这些不属于仓库内容，但**它们对后续协议维护有实际价值**，
删除前请想清楚：

| 目录 | 内容 | 为什么留 |
| --- | --- | --- |
| `data/reference/player_js/` | 学习通播放器的真实 JS（`index.js`、`videojs-ext.min.js`、`common-lang.js`…） | **enc 签名算法的原始依据**。哪天签名变了，得回这里对照 JS 才能推出新算法；另外 `player_js/index.js_v_2026-0902-1207` 的版本号正是 `VIDEO_PLAYER_REFERER` 里那一串 |
| `data/debug/*.json` | 抓包样本（`captured_heartbeat` / `captured_quiz` / `captured_submit`） | 端点注册表里 `Calibration.VERIFIED` 的依据；`scripts/import_legacy_fixtures.py` 用它生成 `tests/fixtures/` |
| `data/debug/cards_*.html`、`chapters_*.html` | 章节与卡片页快照 | 解析层的离线回归素材 |
| `data/debug/search_*.html`、`shots/*.png`、`page-*.png` | 搜索页与截图 | 一次性的调研痕迹，**没有复用价值**，可随时清 |
| `data/chaoxing.db` | 旧库（账号 + 题库缓存） | 已由 `cx db import-legacy` 承接（实测 2/2 账号 + 7/7 题库可导入）；留作回滚保险 |

**协议校准的正规入口是 `cx dev capture`**（录制新样本）与 `cx dev probe endpoints`（核实现有注册表），
而不是继续往 `data/debug/` 里堆文件。

