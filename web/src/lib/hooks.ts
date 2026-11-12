import useSWR, { type SWRConfiguration } from "swr"

import { apiGet, apiPost } from "@/lib/api"
import type { components, paths } from "@/lib/api-types"
import type { Kpi } from "@/pages/dashboard"

// 类型统一从这里出口：页面之间不互相 import（避免循环依赖）。
// DTO 一律是 `api-types.ts`（后端 OpenAPI 的生成物）的别名：手抄字段等于允许两边漂移。
type Schemas = components["schemas"]

// Kpi 是唯一的例外：后端 `/stats/kpi` 没有 response_model（返回 `dict[str, Any]`），
// 生成出来只会是 `{ [key: string]: unknown }`，用不了 —— 后端补上响应模型后一并换成别名。
export type { Kpi } from "@/pages/dashboard"

/** 账号（与后端 `/accounts` 一致；**不含任何凭据**）。 */
export type Account = Schemas["AccountOut"]

/** 课程（与后端 `/courses` 一致）。 */
export type Course = Schemas["CourseOut"]

/** 任务点（与后端 `/courses/{id}/points` 一致；**不含 payload**）。 */
export type TaskPoint = Schemas["TaskPointOut"]

/** 账户组（虚拟账户组：批量操作与共享的入口）。 */
export type AccountGroupItem = Schemas["GroupItem"]

/** 按天活动（与后端 `/stats/daily` 一致）。 */
export type DailyPoint = Schemas["DailyPoint"]

/** 刷课控制状态（`/study/runs`、`/study/pause|resume` 的响应）。 */
export type StudyRunState = Schemas["StudyRunState"]

/** 正在刷课的账号（`/study/active` 的响应）。 */
export type ActiveRuns = Schemas["ActiveRunsOut"]
export type ActiveAccount = Schemas["ActiveAccountOut"]

/** 共享的 SWR 配置：出错不重试（权限问题重试一百次也还是不行）。 */
const SWR_OPTS: SWRConfiguration = {
  shouldRetryOnError: false,
  revalidateOnFocus: false,
}

/** 组列表。 */
export function useGroups() {
  return useSWR<AccountGroupItem[]>(
    "/groups",
    (key: string) => apiGet<AccountGroupItem[]>(key),
    SWR_OPTS,
  )
}

/** 账号列表（可按组收窄；组不放松可见性，两个条件是叠加的）。 */
export function useAccounts(groupId?: number | null) {
  const path = groupId ? `/accounts?group_id=${groupId}` : "/accounts"
  return useSWR<Account[]>(path, (key: string) => apiGet<Account[]>(key), SWR_OPTS)
}

/** 某账号的课程列表。 */
export function useCourses(accountId: number | null) {
  return useSWR<Course[]>(
    accountId ? `/courses?account_id=${accountId}` : null,
    (key: string) => apiGet<Course[]>(key),
    SWR_OPTS,
  )
}

/** 某课程的任务点（按播放顺序）。 */
export function usePoints(courseId: number | null) {
  return useSWR<TaskPoint[]>(
    courseId ? `/courses/${courseId}/points` : null,
    (key: string) => apiGet<TaskPoint[]>(key),
    SWR_OPTS,
  )
}

/** KPI（可按账号收窄）。 */
export function useKpi(accountId: number | null) {
  const path = accountId ? `/stats/kpi?account_id=${accountId}` : "/stats/kpi"
  return useSWR<Kpi>(path, (key: string) => apiGet<Kpi>(key), SWR_OPTS)
}

/** 按天活动。 */
export function useDaily(accountId: number | null, days = 91) {
  const params = new URLSearchParams({ days: String(days) })
  if (accountId) params.set("account_id", String(accountId))
  const path = `/stats/daily?${params.toString()}`
  return useSWR<DailyPoint[]>(path, (key: string) => apiGet<DailyPoint[]>(key), SWR_OPTS)
}

/**
 * 当前正在刷课的账号（并发模式常开）。
 *
 * `refreshInterval` 是刻意保留的：跑批可能在 **CLI 进程**里，它的 SSE 事件不会
 * 进入 API 进程的事件流；活跃状态只能从数据库读取。3 秒一次只为这一块小数据，
 * 比把整个仪表盘改成轮询便宜。
 */
export function useActiveRuns(enabled = true) {
  return useSWR<ActiveRuns>(
    enabled ? "/study/active" : null,
    (key: string) => apiGet<ActiveRuns>(key),
    { ...SWR_OPTS, refreshInterval: 3000 },
  )
}

/** 刷课运行状态（缺省 = 后端权限范围内可见的全部账号）。 */
export function useStudyRuns() {
  return useSWR<StudyRunState[]>(
    "/study/runs",
    (key: string) => apiGet<StudyRunState[]>(key),
    SWR_OPTS,
  )
}

/**
 * 新建账号的入参。
 *
 * 字段名一律取自生成类型（不再手抄），只有可选性要改回来：`note`/`priority` 在 schema 里带
 * `default`，而生成器把"有默认值"当成"必填" —— 对**请求体**这不成立（不传后端就补默认值）。
 */
type AccountCreateInput = Pick<Schemas["AccountCreate"], "username" | "password"> &
  Partial<Pick<Schemas["AccountCreate"], "note" | "priority">>

/** 新建超星账号（密码只在提交时出现，响应里连密文都没有）。 */
export function createAccount(input: AccountCreateInput) {
  return apiPost<Account>("/accounts", input)
}

/** 触发一次课程列表扫描。 */
export function scanCourses(accountId: number) {
  return apiPost<Schemas["ScanResult"]>("/courses/scan", {
    account_id: accountId,
  } satisfies Schemas["ScanRequest"])
}

/** 触发一门课的任务点扫描。 */
export function scanPoints(accountId: number, courseKey: string) {
  return apiPost<Schemas["PointScanResult"]>("/courses/points", {
    account_id: accountId,
    course_key: courseKey,
  } satisfies Schemas["PointScanRequest"])
}

/** 勾选要刷的课程（替换式）。 */
export function selectCourses(accountId: number, courseKeys: string[]) {
  return apiPost<Schemas["SelectResult"]>("/courses/select", {
    account_id: accountId,
    course_keys: courseKeys,
  } satisfies Schemas["SelectRequest"])
}

/** 刷一个账号（同步执行；大批量用 CLI）。 */
export function runStudy(accountId: number, courseIds: number[] = []) {
  return apiPost<Schemas["RunResult"]>("/study/run", {
    account_id: accountId,
    course_ids: courseIds,
  } satisfies Schemas["RunRequest"])
}

/** 请求暂停：运行器在下一个检查点交出控制权，本次到此为止。 */
export function pauseStudy(accountId: number) {
  return apiPost<StudyRunState>("/study/pause", {
    account_id: accountId,
  } satisfies Schemas["ControlRequest"])
}

/** 请求继续（重新执行刷课即从断点接着跑）。 */
export function resumeStudy(accountId: number) {
  return apiPost<StudyRunState>("/study/resume", {
    account_id: accountId,
  } satisfies Schemas["ControlRequest"])
}

// 账号登录的请求体：两处 `LoginRequest` 撞名，生成器给 schema 名加了模块前缀，
// 按 URL 取更稳（URL 变了本来就是破坏性变更）。
// `Partial` 同理 `AccountCreateInput`：`captcha` 带默认值但不必传。
type AccountLoginBody = Partial<
  paths["/api/accounts/{account_id}/login"]["post"]["requestBody"]["content"]["application/json"]
>

/** 触发一次超星账号登录。 */
export function loginAccount(accountId: number, password?: string) {
  return apiPost<Schemas["LoginResultOut"]>(
    `/accounts/${accountId}/login`,
    (password === undefined ? {} : { password }) satisfies AccountLoginBody,
  )
}
