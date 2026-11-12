# 学习通 Android 7.0.4 全量网络协议逆向记录（2026-11-11）

## 采集环境

| 项 | 值 |
|---|---|
| App | `com.chaoxing.mobile` 7.0.4（versionCode 10995） |
| APK SHA-256 | `849a714d83d2fe1ec8cb8fd43e0db408122957cf50906b7b83501d2dcfec99e3` |
| 设备 | Redmi 25080RABDC，Android 16（arm64），KernelSU + LSPosed |
| 抓包 | mitmproxy 12.3.1，`adb reverse tcp:8080 tcp:8080` + 系统代理；AlwaysTrustUserCerts v1.3 |
| 动态 | Frida 17.26.2；REA 6.8.0（静态/设备枚举） |
| 规模 | 1,047 条事务、76+ 个 host；其中 `mooc1-api` 占多数 |

## 静态 REA 结论

`search_android_classes` 对 APK 只返回 **336 个 top-level class**，引擎为 jadx-headless 0.7.1。
这些是 SecNeo 壳层/路由桩，不是业务网络类；`com.chaoxing.*` 的真实 Activity、网络层、测验类在
运行时由壳解密，静态 DEX 不可见。因此全量协议以**运行时 H5 + 原生请求**为准，REA 负责
设备/进程/会话事实，抓包负责协议事实。

## 主链路与端点

| 域 | 端点/路径 | 用途 |
|---|---|---|
| 登录 | `POST sso.chaoxing.com/apis/login/userLogin.do` | 账号登录 |
| 配置 | `GET learn.chaoxing.com/apis/service/appConfig` | App 远程配置 |
| 首页 | `GET apps.chaoxing.com/apis/recent/getRecord.jspx`、`GET notice.chaoxing.com/apis/notice/getDataChangeStatus`、`home-yd.../getAdsRecommend` | 最近使用/通知/运营位 |
| 课程 | `GET mooc1-api.chaoxing.com/gas/clazz`、`gas/clazzperson`、`gas/knowledge` | 课程、班级、知识点 |
| 章节 | `GET mooc1-api.chaoxing.com/knowledge/cards`、`GET job/submitstudy` | 卡片、学习进度上报 |
| 视频 | `GET .../mooc-ans/richvideo/initdatawithviewernew`、`.../richvideo/get-video-points`、`.../richvideo/allsubtitle` | 视频元数据/字幕/知识点 |
| 视频心跳 | `GET .../mooc-ans/multimedia/log/a/{cpi}/{dtoken}` | 真实进度上报 |
| 测验入口 | `GET mooc1-api.chaoxing.com/mooc-ans/android/mworkspecial` | 手机 App 作业入口，302 |
| 测验页 | `GET .../mooc-ans/work/phone/work` | 手机题目页，302/200 |
| 已批阅题 | `GET .../mooc-ans/work/phone/selectWorkQuestionYiPiYue` | 已交卷/已批阅题目与答案 |
| 交卷 | `POST .../mooc-ans/work/addStudentWorkNew` | 手机表单 action 的真实提交端点 |
| 笔记 | `noteyd.chaoxing.com/apis/*` | 笔记/草稿 |
| 消息 | `im.chaoxing.com/apis/message/getSessionTopList` | 会话列表 |
| 用户 | `useryd.chaoxing.com/apis/user/getUser` | 用户资料 |

> `mooc1-api` 与 Web 的 `mooc1` 是**两条链路**；手机 App 的作业页/提交页面走
> `mooc1-api`，表单 action 是 `addStudentWorkNew`（无 Web 后缀）。

## 视频心跳（真机抓包）

实例：

```
GET https://mooc1-api.chaoxing.com/mooc-ans/multimedia/log/a/100000001/f57d...
  ?otherInfo=nodeId_1221610460-cpi_100000001-rt_d-ds_1-ff_d-be_0_0-vt_1-v_6-enc_59eb...
  &courseId=...&playingTime=0&duration=483&akid=null
  &jobid=1615189004295723&clipTime=0_483&clazzId=...
  &objectId=57c1c2425d808823dd4d8ce5b52bf402&userid=...
  &isdrag=3&enc=...&rt=0.9&dtype=Video&view=json
  &cxtime=1794404956457&cxcid=...
```

后续正常播放：`playingTime=58`，`isdrag=0`，`rt=0.9`，回执 `{"isPassed":false}`。
`enc` 算法仍为已有的 md5 规则；`clipTime=0_{duration}`。

与当前客户端的差异：
- `view` 应为 `json`（App），不是 `pc`（Web）；
- 首跳 `isdrag=3`，后续正常播放 `isdrag=0`；
- App 带 `akid=null`、`cxtime`、`cxcid`（`cxtime/cxcid` 为端侧会话标记，服务端未证明必需）。

## 测验/答题链路

1. 卡片提供：`workid`（hex）、`jobid=work-<hex>`、`courseId`、`clazzId`、`knowledgeid`、`cpi`、`enc`、`ktoken`。
2. 手机入口：
   `GET /mooc-ans/android/mworkspecial?workid=...&jobid=...&originJobId=...&needRedirect=true&courseid=...&knowledgeid=...&userid=...&ut=s&clazzId=...&cpi=...&ktoken=...&enc=...`
   → 302 `GET /mooc-ans/work/phone/work?...`
   → 未交卷页含 `form1`，action 形如
   `/mooc-ans/work/addStudentWorkNew?keyboardDisplayRequiresUserAction=1&_classId=...&courseid=...&token=<enc_work>&workAnswerId=...&workid=...&cpi=...&jobid=...&knowledgeid=...&originJobId=...`
3. 题目页隐藏字段（form1）：
   `courseId`、`classId`、`enc_work`、`workAnswerId`、`workid`、`cpi`、`jobid`、`knowledgeid`、`originJobId`、
   逐题 `answertype{id}`、空 `answerwqbid`。
4. 提交 body：
   - `answer{questionId}`（单选/多选字母串、判断 true/false、连线 JSON、填空/简答原样）
   - `answertype{questionId}`（0 单选、1 多选、3 判断、2 填空、4 简答、6 论述、11 连线…）
   - `answerwqbid=id1,id2,...,`（**末尾逗号**）
   - 隐藏字段原样回传 + `userId` / `workTimesEnc` / `randomOptions` / `isAccessibleCustomFid`
5. 交卷前预检：`GET /mooc-ans/work/verify/code`，成功返回 `{"status":true,"enc":"..."}`，
   将 `enc` 写入 `workTimesEnc`；若返回验证码错误，必须人工读 `verifyCode/stuWork` 图片。

**当前脚本仍可能失败的两个点**：
- 使用 Web 的 `addStudentWorkNewWeb` 而不是手机 H5 的 `addStudentWorkNew`；
- 题目页只认部分 `id=questionN`，漏掉 `name=answertypeN`（已在本仓库修复，并增加
  `form_ids` 完整性闸门，缺一题不交卷）。

## 已确认的代码修复

- `client/parser/quiz.py`：`answertypeN` 纳入题目 ID，按文档位置排序。
- `core/services/study.py`：表单 ID 集完整性校验；缺 ID/缺答案拒绝提交；文档完成只认本次目录页。
- `client/api/quiz.py`：手机题目页兜底提交端点改为 `addStudentWorkNew`。
- `client/api/video.py`：`view=json`、`isdrag` 首跳 3/后续 0、`akid=null`。
- `client/endpoints/quiz.py`：注册手机入口/题目页/已批阅/提交端点。

## 未完成/待验证

- 未交卷测验的真实提交回执（本账号燃料油课程测验均已交卷/过期；未做越权重交）。
- PPT/文档的手机端完成接口（本次只抓到视频/测验；文档需另抓 `mworkspecial` 文档页）。
- 考试（exam）链路未取得未开始考试。
- `view=json`/`isdrag=0` 的线上完成回执需用未完成视频验证。

## PPT / PPTX 完成（真机实测 2026-11-11）

打开燃料油 `1.1.2 课程介绍(2).pptx` 时，App 发出：

```
GET https://mooc1-api.chaoxing.com/mooc-ans/job/document
  ?jobid=1663125853127222
  &knowledgeid=1221610457
  &courseid=266133841
  &clazzid=158242229
  &jtoken=a5cb143c...
  &checkMicroTopic=true
  &microTopicId=undefined
  &courseEngineInfo=false
  &_dc=1794407028700
```

请求头：`X-Requested-With: XMLHttpRequest`；
`Referer: https://mooc1-api.chaoxing.com/ananas/modules/zt/ppt/index.html?v=122026-1110-2030`。

回执：

```json
{"msg":"添加考核点成功","status":true}
```

回到任务页确认：**“任务点已完成”绿标**。

与旧实现差异：旧代码走
`https://mooc1.chaoxing.com/ananas/job/document`，缺 `checkMicroTopic` /
`microTopicId` / `courseEngineInfo`，且 `_dc` 为空；手机 App 实际使用
`mooc1-api.../mooc-ans/job/document`。项目和端点已改为真机口径。
