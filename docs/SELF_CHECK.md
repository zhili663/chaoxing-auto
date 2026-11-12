# 部署后自测清单

> 这份清单对应 `ACCEPTANCE.md` 里**唯一一项无法离线验证**的验收：与真实学习通的端到端链路。
> 它按"命令 → 该看到什么 → 不对怎么办"来写，照抄即可，不需要回头翻别的文档。
>
> 全套约 5 分钟。任何一步的**实际输出**都比本文的示例更有发言权 ——
> 与示例不符时以实际为准，并按下表排查。

## 0. 前置检查（30 秒）

```bash
uv run cx doctor --full
```

**该看到**：`python` / `package` / `lock` / `config` / `exposure` / `secret_key` / `data_dir` /
`database` / `frontend` 全部 `PASS`；`container` / `api/health` / `spa` / `sse` 也是 `PASS`；
`accounts` 这一步在**还没建号时是 `SKIP`**（"库里还没有账号"），这是正常的。

| 不对 | 怎么办 |
| --- | --- |
| 有 `FAIL` | 按该行提示处理；`--fix` 能自动修的会自己修（比如缺 `SECRET_KEY`） |
| `database` 是 `SKIP` | `uv run cx db upgrade` |
| `frontend` 是 `SKIP` | `cd web && pnpm install && pnpm build` |

## 1. 建账号（1 分钟）

超星账号与平台账号是两回事：**先建超星账号实体，再谈登录**。

```bash
uv run cx account add -u <手机号> -p            # -p 留空 → 交互式输入，不回显
```

**该看到**：`已新增账号 1（138xxxxxxxx）`，并提示下一步执行 `cx auth login`。

```bash
uv run cx account list
```

**该看到**：一行账号记录，`状态 pending`、`登录态` 列为空（还没登录）。

> 也可以走网页：起 `cx serve` → 打开站点 → 注册管理员（首个用户）→ 课程页左区「新建账号」。

## 2. 登录超星（1 分钟）

```bash
uv run cx auth login -u <手机号> -p --account-id 1
```

**该看到**：`登录成功 账号 1（138xxxxxxxx）uid=...`

| 不对 | 怎么办 |
| --- | --- |
| `需要验证码` | 按提示重跑并加 `--captcha <验证码>` |
| 提示密码错误但确定没输错 | 加 `--encoding base64` 再试（少数账号的密码编码方式不同） |
| 反复失败 | `uv run cx doctor --full` 看 `accounts` 那一行的说法 |

```bash
uv run cx doctor --full
```

**该看到**：`accounts` 从 `SKIP` 变成 **`PASS`「账号 xxx 登录态有效（真实请求已校验）」** ——
这一步是**真的打了一次学习通**，不是看配置。

## 2.5 视频心跳资格检查（30 秒，可选）

```bash
uv run cx dev probe heartbeat --account 1 --point <任务点ID>
```

**该看到**：前 / 后两个 dtoken、一行结论（已激活 / 未激活）、一行适用性说明。

| 不对 | 怎么办 |
| --- | --- |
| 结论「未激活」 | 这个视频从未被真实播放过 —— 在浏览器里播几秒，再重跑本命令 |
| `没有登录态` | 回到第 2 步 |
| 卡片缺字段 | 先重扫任务点（第 5 步）：缺 `otherInfo` / `attDurationEnc` 这类字段，心跳必 403 |

## 3. 扫课程（30 秒）

```bash
uv run cx course scan --account 1
```

**该看到**：`扫描完成 共 N 门（新增 N / 更新 0）`

```bash
uv run cx course list --account 1
```

**该看到**：课程表格，含「课程键」形如 `265361351:151148348`。

## 4. 勾选要刷的课（10 秒）

```bash
uv run cx course select --account 1 <课程键>
```

**该看到**：`已勾选 1 门课程`。

> 「刷什么」完全由这一步决定 —— 代码里没有任何默认课程。

## 5. 扫任务点（30 秒）

```bash
uv run cx course points --account 1 --course <课程键>
```

**该看到**：`章节 N · 新增 M / 更新 0`，以及**类型分布**（`video` / `document` / `quiz` 各有几个）。

| 不对 | 怎么办 |
| --- | --- |
| `没有解析到章节` | 页面结构可能变了；`cx dev capture browser`（orca-cli 驱动，产 HAR）录一份新样本后 `cx dev probe endpoints` 核对注册表 |
| 任务点数明显偏少 | 同样先录样本对照；`task_points.payload` 里应能看到 `otherInfo` 等透传字段 |

## 6. 试刷 3 个任务点（1~2 分钟）

先用 `--limit` 试跑，确认链路通了再上量：

```bash
uv run cx study run --account 1 --limit 3
```

**该看到**：`播放单元 X · 完成 Y · 失败 0 · 待人工 0 · 跳过 Z`，以及逐条明细。
视频会按 `心跳间隔 12~20 秒` 的节奏推进（**不下载任何视频流**），所以 1~2 分钟是正常的。

| 不对 | 怎么办 |
| --- | --- |
| 大量 `待人工` | 学习通要求人脸抓拍或验证码 → 到网页上人工处理，之后重跑 |
| 大量 `失败` | 看明细里的原因；`3004` 通常是登录态过期（重跑第 2 步） |
| `没有登录态` | 回到第 2 步 |

## 7. 查四类记录（30 秒）

一条命令把四类记录都打出来：

```bash
uv run python scripts/acceptance_e2e.py --account 1 --limit 3
```

**该看到的四类记录**（这是"可观测性四通道"是否真的落地的判据）：

| 通道 | 表 | 该看到什么 |
| --- | --- | --- |
| 刷课记录 | `task_events` | `started` / `finished` 事件，含课程与任务点 |
| 刷题记录 | `quiz_attempts` | 跑过的测验（没测验任务点时是 0，正常） |
| 行为审计 | `audit_logs` | 网页上的写操作；命令行操作不经过 API，因此这里可能为空 |
| app 日志 | `data/logs/app.log` | 结构化日志行，含每次心跳与判定结果 |

**注意**：`task_points` 里 `status=done` 的条目数是最终判据 —— 它由**服务端回执**决定，不是本地跑完就算。

## 8. 收尾

跑通了就说明端到端链路成立。剩下的按需：

```bash
uv run cx study run --account 1        # 去掉 --limit，刷完已勾选的课
uv run cx stats --account 1            # 看进度与热力
uv run cx serve                        # 起网页（同源托管前端产物）
```

网页上能看到的东西：仪表盘 KPI 与 13 周热力图、课程工作台（左账号/中课程/底部实时日志/右控制台）、
刷题页题库、用户管理、设置 —— 实时数据与日志全部走 SSE，**页面不做轮询**。

## 出问题时先看哪里

| 现象 | 先看 |
| --- | --- |
| 服务起不来 | `journalctl -u chaoxing -n 50 --no-pager`（systemd）或 `cx serve --check-only` |
| 心跳一直 403 | `uv run cx dev probe heartbeat --account 1 --point <ID>` 先看 dtoken 是否激活；403 是**业务拒绝**（播放器 JS 里那颗 `code=6`），见 `docs/MOOC_ANS_403.md` |
| 页面 404 但接口正常 | 前端产物没构建（`web/dist/index.html` 不存在） |
| 日志不刷新 | 反代是否关了缓冲（`deploy/nginx.conf` 里的 `proxy_buffering off`） |
| 提示"数据库锁" | 确认只有一个进程在用这个库（`workers` 必须为 1） |
| 想改行为参数 | `cx settings show` 看可调项；`cx settings set chaoxing.video_rate 1.0` 热改（执行层仍钳到 1.0） |

## 把结果记下来

跑完请把**第 6 步与第 7 步的实际输出**贴回验收记录 ——
`docs/ACCEPTANCE.md` 第 4 条会据此从"未验证"改成"已实测"，并附上真实数字。
在那之前，它一直是缺口。
