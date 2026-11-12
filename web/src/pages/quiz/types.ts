import type { components } from "@/lib/api-types"

/** 刷题页的展示口径（形状**只有一份**：由后端 OpenAPI 生成，见 `@/lib/api-types`）。 */

/** 一条交卷记录（`GET /quiz/attempts` 的响应项）。 */
export type AttemptRow = components["schemas"]["AttemptOut"]

/** 一条题库记录（`GET /quiz/cache` 的响应项）。 */
export type AnswerItem = components["schemas"]["AnswerItem"]

/** 来源标注：人工录入的最可信，命中次数代表"这题考过几次"。 */
export const SOURCE_LABEL: Record<string, string> = {
  manual: "人工",
  model: "模型",
  cache: "缓存",
}

/** `detail` 里已知字段的中文名；没登记过的键按原名列出（不丢信息）。 */
export const DETAIL_LABEL: Record<string, string> = {
  confidence: "作答置信度",
}

/** 交卷时间（本地可读；缺失给破折号，别让单元格空着像坏了）。 */
export function formatStamp(value: string | null): string {
  if (!value) return "—"
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return "—"
  return date.toLocaleString("zh-CN", { hour12: false })
}

/** 作答进度：`answered/total` —— 交卷与否看的是这份进度有没有推进。 */
export function questionText(row: AttemptRow): string {
  return `${row.answered_count}/${row.question_count}`
}

/** 手机号脱敏（与账号台账同一口径）：列表里不露全号，学号同样打码。 */
export function maskAccount(username: string): string {
  const name = username.trim()
  if (/^1\d{10}$/.test(name)) return `${name.slice(0, 3)}****${name.slice(7)}`
  if (name.length <= 4) return name
  return `${name.slice(0, 2)}****${name.slice(-2)}`
}
