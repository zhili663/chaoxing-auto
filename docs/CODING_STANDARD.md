# 编码规范（本项目的"模范写法"）

> 这份文档不是装饰。每一条都由 `tools/check_style.py` 机械校验，
> 你可以随时执行 `python tools/check_style.py` 看当前达标情况。

## 0. 分层与依赖方向

```
app/           HTTP 适配层：路由、请求/响应模型、依赖注入
core/chaoxing/ 协议层：学习通的请求构造与页面解析
core/scheduler/ 调度层：任务编排、进度同步、报表聚合
core/storage/   存储层：SQLite 与仓储
core/ai/        智能层：答题引擎、联网检索
core/utils/     工具层：日志、时间、加密、校验
```

**依赖只能单向向下**：`app` → `core.scheduler` → `core.chaoxing` → `core.storage` / `core.utils`。

禁止反向 import（例如 `core/` 里 import `app/`），也禁止同层互相调用形成环。
唯一的例外是 `app/deps.py` 可以 import `core.*`（它是装配点）。

**为什么**：协议层与调度层要能脱离 HTTP 单独跑（`tools/` 下的脚本正是这么用的）。

## 1. 模块头

每个模块以三引号 docstring 开头，说清三件事：**这个模块负责什么、不负责什么、关键约束**。

```python
"""进度同步器。

需求原文：*"后台任务每 15 分钟自动更新一次进度"*。

实现要点
--------
* 每 ``settings.progress_sync_interval`` 秒执行一次；
* **不实时访问学习通**：进度以本地任务点表为准。
"""
```

模块开头统一加 `from __future__ import annotations`，让注解写成 `list[str]` 而不必担心版本。

## 2. 函数与类

**公共成员必须有 docstring**（`_` 开头的私有成员可省略，但复杂逻辑仍需注释）。
风格用 **Sphinx 字段**，与既有代码一致：

```python
async def available_courses(self, account_id: int) -> dict[str, Any]:
    """列出该账号的课程，并判断哪些可以直接开刷。

    "可刷"由三件事共同决定：**已扫描**、**还有剩余任务点**、**登录态有效**。

    :param account_id: 账号 ID
    :returns: 可直接构造 ``AvailableCoursesResponse`` 的字典
    """
```

**参数与返回值必须有类型注解**（`self`/`cls` 除外）。返回值是 `None` 也要显式写 `-> None`。

命名：类用 `CapWords`，函数/变量用 `snake_case`，模块级常量用 `UPPER_CASE`。

## 3. 注释：只解释"为什么"

代码说"做了什么"，注释说"**为什么这么做**"。禁止复述代码。

```python
# ✅ 好：解释了一个反直觉的决定
# 倍速决定"我们要假装看了多久"，因此估算真实耗时必须除以倍速；
# max(..., 0.1) 是防御性下限，避免配置成 0 时除零。
rate = max(float(self.settings.video_rate), 0.1)

# ❌ 差：把代码翻译一遍，零信息量
# 把 rate 设为 video_rate
rate = float(self.settings.video_rate)
```

以下场景**必须**有注释：

1. **绕过某个坑**：写清踩坑现象与实测结论。例：
   `# 实测 2026-09：cn.bing.com 会 302 回首页（0 条结果），必须用 www.bing.com`
2. **魔数与协议细节**：给出字段含义与来源。
3. **故意的"不优化"**：说明权衡。例：`# 每门课多一条 SQL 换取判定准确，账号数不影响单账号开销`
4. **兼容性妥协**：说明兼容对象与移除条件。

注释里禁止写"待办占位"（`# TODO: 以后再说`）；要做的事要么现在做完，要么写进文档的待办清单。

## 4. 错误处理

* 对外抛出的异常必须继承 `core.exceptions.ChaoxingAutoError`，并把"是否可重试"表达清楚；
* **宽泛捕获必须标注理由**：`except Exception as exc:  # noqa: BLE001 - 同步失败不能影响服务`；
* 绝不用 `except: pass` 吞掉异常；至少要 `log.warning`／`log.debug`；
* 判"成功"要看**业务字段**，不能只看 HTTP 200。例：文档任务的响应体里
  `{"status": false, "msg": "无效的请求参数"}` 就是失败。

## 5. 异步与并发

* 所有 I/O 走 `async`；禁止在协程里调用阻塞库（需要时用 `asyncio.to_thread`）；
* 后台循环必须能被 `stop()` 干净取消，并且**循环体不能抛穿**：

```python
while not self._stopping:
    try:
        await self._tick()
    except asyncio.CancelledError:
        raise  # 取消要向上传播，否则无法退出
    except Exception as exc:  # noqa: BLE001 - 单轮失败不能终止服务
        log.error("异常：%s", exc, exc_info=True)
    await asyncio.sleep(interval)
```

* 每个账号内的请求串行（`REQUEST_MIN_INTERVAL` 控速），账号之间才用 asyncio 并发。

## 6. 存储

* 表结构变更通过 `core/storage/db.py` 的 `_ensure_column` 做**幂等迁移**，不手改线上库；
* **不以缓存列为事实来源**：`courses.done_points` 这类冗余列只用于展示加速，
  判定逻辑一律回查 `task_points`；
* SQL 一律参数化（`?` 占位），禁止字符串拼接用户输入。

## 7. API 约定

* 路径用名词、动词交给 HTTP 方法：`GET /courses/available`、`POST /select_course`；
* 请求体字段 `account_id` 与 `username` 二选一，统一由 `resolve_account_id` 解析；
* 响应模型放在 `app/schemas.py`，**新增字段必须有默认值**（老客户端不能被破坏）；
* 路由函数只做"解析参数 → 调用 core → 转换模型"，业务逻辑一律下沉到 `core/`。

**准入分两类，别混用**：

* **机器凭证** `X-API-Token`（`require_token`）—— 管理接口与写接口用，可跨账号；
* **人的凭证** `X-Viewer-Token`（`require_read_access`）—— 网页读接口用，由手机号
  换取，服务端据此把查询锁死在本人账号内。

读接口里**绝不允许**直接采用客户端传来的 `account_id`，必须走
`resolve_read_account_id(container, viewer, ...)` —— 它会忽略网页访问者传来的任何
账号参数。少了这一步，改个查询串就能看别人的进度。

另外：凡是会吐出账号列表的接口（`/accounts`、`/status`），都必须受机器凭证保护。
把它们匿名开放等于泄露全部手机号，再拿号码去换查看凭证即可绕过整套准入。

## 8. 行宽与文件长度

* **代码行** ≤ 100 字符；
* **docstring / 注释行** ≤ 110 字符 —— 放宽的理由是强行折行会破坏 RST 表格与
  首行对齐，收益为负。中文按 1 字符计，实际显示宽度约 2 倍，所以中文行仍需更短；
* 单文件建议 ≤ 500 行；超过时优先按"职责"拆分，而不是按"行数"拆。

## 9. 前端（`app/static/`）

* 单文件、零外部依赖（不引 CDN），避免服务器网络受限导致页面白屏；
* **页面内不得出现任何凭据**；令牌由访问者在浏览器端填写，只存 `localStorage`；
* 渲染用户可控文本一律用 `textContent` 或转义，禁止直接拼 `innerHTML`。

## 10. 自检清单

```bash
python tools/check_style.py     # 编码规范
python tools/check_deploy.py    # 部署前跨平台/依赖/安全自检
python tools/selftest.py        # 单元自测（73 项）
python tools/smoke_api.py       # 接口冒烟（临时库 + 真实 HTTP）
```

四项全绿才算"可以交付"。
