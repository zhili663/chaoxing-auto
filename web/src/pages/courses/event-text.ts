import type { DomainEvent } from "@/lib/events"

/** 事件级别（决定「最近事件」一行的颜色）。 */
export type EventLevel = "info" | "warn" | "error"

/** 一条可读事件。 */
export interface EventText {
  text: string
  level: EventLevel
}

/** 「最近事件」列表里的一行。 */
export interface RecentEvent {
  id: number
  at: number
  text: string
  level: EventLevel
}

/**
 * 哪些事件意味着"数据已落库、值得重新取数"。
 *
 * 进度类事件太密（一秒好几条），只在结束/落库类事件上刷新，避免把 SWR 打爆。
 */
export function shouldRefresh(type: string): boolean {
  return (
    type === "TaskPointFinished" ||
    type === "QuizAnswered" ||
    type === "CourseScanned" ||
    type === "StudyRunFinished"
  )
}

/**
 * 领域事件 → 一行中文（页面「最近事件」与控制台的排障提示共用）。
 *
 * 位置：`@/pages/courses/event-text`。
 * 签名：`describeEvent(event: DomainEvent): EventText`。
 */
export function describeEvent(event: DomainEvent): EventText {
  const payload = event.payload
  switch (event.type) {
    case "TaskPointStarted":
      return { text: `开始 ${String(payload.title ?? payload.point_id ?? "")}`, level: "info" }
    case "TaskPointProgress":
      return {
        text: `进度 ${String(payload.progress ?? "")}/${String(payload.duration ?? "")}`,
        level: "info",
      }
    case "TaskPointFinished":
      return {
        text: `${String(payload.kind ?? "")} ${String(payload.status ?? "")}${
          payload.detail ? ` · ${String(payload.detail)}` : ""
        }`,
        level: payload.status === "done" ? "info" : "warn",
      }
    case "QuizAnswered":
      return {
        text: `测验 ${String(payload.work_id ?? "")}：${String(payload.answered ?? "")}/${String(
          payload.question_count ?? "",
        )} 题`,
        level: payload.submitted ? "info" : "warn",
      }
    case "HumanRequired":
      return { text: `需要人工：${String(payload.reason ?? "")}`, level: "error" }
    case "SessionExpired":
      return { text: `登录态失效：${String(payload.reason ?? "")}`, level: "error" }
    case "AccountLoggedIn":
      return { text: "登录成功", level: "info" }
    case "CourseScanned":
      return { text: `课程扫描完成：${String(payload.total ?? "")} 门`, level: "info" }
    case "StudyRunFinished":
      return {
        text: `刷课结束：完成 ${String(payload.done ?? "")} · 失败 ${String(payload.failed ?? "")}`,
        level: "info",
      }
    default:
      return { text: JSON.stringify(payload), level: "info" }
  }
}
