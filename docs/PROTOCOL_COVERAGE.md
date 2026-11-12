# 协议覆盖清单（PROTOCOL_COVERAGE）

> 由 `cx dev probe coverage` 自动生成，请勿手工编辑。
> 目标（见 `docs/REFACTOR_PLAN.md` §5.14）：**把刷课链路上真实存在的包与 API call 还原齐全**，
> 缺一条要写明原因，而不是默默略过。

## 统计

- 已注册端点：**32**（VERIFIED 13 / TEMPLATE 18 / VOLATILE 1）
- 已有测试样本：**4**
- 本次对账的录制请求：46 条，其中未建模 0 条

## 端点明细

| 端点 | 方法 | 路径 | 置信度 | 测试样本 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `login_page` | GET | `/login` | verified | — | 登录页：预取 fid 与初始 cookie，并检测验证码开关 |
| `login_submit` | POST | `/fanyalogin` | verified | — | 账号密码登录 |
| `captcha_image` | GET | `/num/code` | verified | — | 图形验证码图片 |
| `portal_entry` | GET | `/base` | verified | — | 登录后的门户页；其响应的 Set-Cookie 才包含完整会话（_uid / _d 等） |
| `course_list` | GET | `/visit/courselistdata` | verified | course_list.html | 课程列表主来源 |
| `course_list_legacy` | GET | `/visit/interaction` | template | — | 老版课程列表 HTML（主来源失效时的兜底解析源） |
| `course_entry` | GET | `/mooc-ans/visit/stucoursemiddle` | verified | — | 课程入口 |
| `chapter_content` | GET | `/mooc2-ans/mycourse/studentcourse` | verified | — | 章节内容页（含任务点卡片）；**必须带 stuenc 与 t**，否则服务端返回权限错误 |
| `chapter_cards` | GET | `/knowledge/cards` | template | job_card_video.json | 章节任务点卡片（JSON） |
| `chapter_study_page` | GET | `/mycourse/studentstudy` | template | — | 真机录制命中（2026-10-11） |
| `chapter_study_mooc2` | GET | `/mooc2-ans/mycourse/stu` | template | — | 真机录制命中（2026-10-11） |
| `chapter_tree_ajax` | GET | `/mooc-ans/mycourse/studentstudyAjax` | template | — | 真机录制命中（2026-10-11） |
| `chapter_course_list` | GET | `/mooc-ans/mycourse/studentstudycourselist` | template | — | 真机录制命中（2026-10-11） |
| `chapter_cards_mooc` | GET | `/mooc-ans/knowledge/cards` | template | — | 真机录制命中（2026-10-11） |
| `course_transfer` | GET | `/mycourse/transfer` | template | — | 真机录制命中（2026-10-11） |
| `video_info` | GET | `/ananas/status/{object_id}` | verified | — | 视频元信息 |
| `video_player` | GET | `/ananas/modules/video/index.html` | verified | — | 播放器页面 |
| `video_heartbeat` | GET | `/mooc-ans/multimedia/log/a/{cpi}/{dtoken}` | verified | — | 播放进度上报 |
| `video_heartbeat_alt` | GET | `/ananas/multimedia/log/a/{cpi}/{dtoken}` | template | — | 备用上报通道（部分课程走 ananas 域名）；主通道连续异常时切换 |
| `document_finish` | GET | `/ananas/job/document` | template | — | PPT / PDF / 文本类任务点完成 |
| `read_finish` | GET | `/ananas/job/readv2` | template | — | 阅读类任务点完成；参数与文档接口一致（作为 document 失败时的回退通道） |
| `chapter_document_marks` | GET | `/mooc-ans/chapter-document/marklists` | template | — | 真机录制命中（2026-10-11） |
| `quiz_redirect` | GET | `/mooc-ans/api/work` | verified | — | 测验换票接口 |
| `quiz_plain_questions` | GET | `/mooc-ans/api/selectWorkQuestion` | verified | quiz_page.html | 明文题干接口（**绕过字体反爬的关键**） |
| `quiz_detail` | GET | `/mooc-ans/work/doHomeWorkNew` | template | quiz_page.html | 题目页（HTML，含题干/选项/隐藏字段） |
| `quiz_submit` | POST | `/mooc-ans/work/addStudentWorkNewWeb` | verified | — | 提交测验 |
| `quiz_work_page` | GET | `/ananas/modules/work/index.html` | template | — | 真机录制命中（2026-10-11） |
| `quiz_work_handle` | GET | `/mooc-ans/workHandle/handle` | template | — | 真机录制命中（2026-10-11） |
| `quiz_validate` | GET | `/mooc-ans/work/validate` | template | — | 真机录制命中（2026-10-11） |
| `quiz_verify_code` | GET | `/verifyCode/stuWork` | template | — | 真机录制命中（2026-10-11） |
| `exam_start` | GET | `/exam-ans/exam/test/reVersionTestStartNew` | template | — | 开始考试：换取试卷 ID 与试题列表 |
| `exam_submit` | POST | `/exam-ans/exam/test/submit` | volatile | — | 交卷 |

## 未建模调用

（未发现）无对应端点的学习通请求。

## 范围外调用（含原因）

这些调用**不在刷课链路上**，但确实出现在录制里 —— 规则与原因集中在 `src/chaoxing/client/coverage.py`，改规则即改本表：

| 规则 | 值 | 原因 |
| --- | --- | --- |
| 主机 | `mobilelearn.chaoxing.com` | 移动端子系统（直播 / 签到 / 活动） |
| 主机 | `robot-lc.chaoxing.com` | 数字人助手接口 |
| 主机 | `robot-static.chaoxing.com` | 数字人助手静态资源 |
| 主机 | `noteyd.chaoxing.com` | 笔记插件 |
| 主机 | `stat2-ans.chaoxing.com` | 前端埋点统计 |
| 路径后缀 | `.js` | 前端脚本 |
| 路径后缀 | `.css` | 前端样式 |
| 路径后缀 | `.cssx` | 前端样式（线上文件名拼作 cssx） |
| 路径后缀 | `.properties` | 前端语言包 |
| 路径后缀 | `.map` | source map |
| 路径后缀 | `.png` | 图片资源 |
| 路径后缀 | `.jpg` | 图片资源 |
| 路径后缀 | `.jpeg` | 图片资源 |
| 路径后缀 | `.gif` | 图片资源 |
| 路径后缀 | `.svg` | 图片资源 |
| 路径后缀 | `.woff` | 字体资源 |
| 路径后缀 | `.woff2` | 字体资源 |
| 路径后缀 | `.ttf` | 字体资源 |
| URL 前缀 | `https://mooc2-ans.chaoxing.com/mooc2-ans/ai-evaluate/` | AI 评价活动（属新增功能范畴） |

## 对账方式

抓包由 **orca-cli** 驱动（本仓库不自建浏览器驱动）：**先开 HAR 再导航**，
HAR 的落盘路径以 `network har stop` 的返回值为准（`start` 不接受路径）。

```bash
orca tab create --json
orca exec --command "network har start" --json
orca goto --url <课程页 URL> --json
orca exec --command "network har stop" --json   # 记下 result.path
cx dev capture sanitize --recorded <har 路径> --out tests/fixtures/recorded/course_session_urls.json
cx dev probe coverage --recorded tests/fixtures/recorded/course_session_urls.json --write
```

（`cx dev capture browser` 是同一套 orca 流程的命令行封装；网页侧检查用 `scripts/web_check.sh`。）

缺样本的端点用 `cx dev capture` 补录后重新生成本文件；
无法还原的**能力级缺口**（不是单条调用）登记在下方：

| 调用 / 能力 | 未还原原因 |
| --- | --- |
| discussion（讨论 / 回复任务点） | 类型已识别但执行器未实现（study._dispatch 对未知类型直接 skip）；无可用样本 |
| exam（考试） | 端点已注册（exam_start / exam_submit），但无 client API 与执行器；交卷接口为 VOLATILE |
| 人机验证（滑块 / 人脸） | 仅验证码图片接口已建模；滑块与人脸走人工介入（HumanRequired） |
