import { useCallback, useEffect, useState } from "react"
import useSWRSubscription from "swr/subscription"

/** 后端领域事件（与 `chaoxing/obs/events.py` 的类名一一对应）。 */
export interface DomainEvent {
  type: string
  payload: Record<string, unknown>
  receivedAt: number
}

/** 需要监听的事件名（与后端类名一致）。 */
export const EVENT_NAMES = [
  "TaskPointStarted",
  "TaskPointProgress",
  "TaskPointFinished",
  "QuizAnswered",
  "CourseScanned",
  "AccountLoggedIn",
  "SessionExpired",
  "HumanRequired",
  "StudyRunFinished",
] as const

/**
 * 订阅 SSE 事件流。
 *
 * 三条设计要点：
 *
 * 1. **没有任何定时器** —— 实时性由服务端推送保证（Biome 里 `setInterval`/`setTimeout`
 *    是硬禁，想加也加不进来）；
 * 2. 断线重连交给浏览器原生的 `EventSource`（它自己会退避重连），我们不去手搓重连逻辑；
 * 3. 回调放在 `useEffect` 里而不是写进订阅闭包 —— 否则父组件每次渲染都会重建连接。
 */
export function useEventStream(onEvent?: (event: DomainEvent) => void) {
  const { data, error } = useSWRSubscription<DomainEvent, Error>(
    "/events",
    (_key: string, options: { next: (error: Error | null, data?: DomainEvent) => void }) => {
      const { next } = options
      const source = new EventSource("/api/events")
      const handlers: Array<[string, EventListener]> = []

      for (const name of EVENT_NAMES) {
        const handler: EventListener = (raw) => {
          const message = raw as MessageEvent<string>
          try {
            next(null, {
              type: name,
              payload: JSON.parse(message.data) as Record<string, unknown>,
              receivedAt: Date.now(),
            })
          } catch {
            // 单条事件解析失败不该断开整条流
          }
        }
        source.addEventListener(name, handler)
        handlers.push([name, handler])
      }

      return () => {
        for (const [name, handler] of handlers) {
          source.removeEventListener(name, handler)
        }
        source.close()
      }
    },
  )

  useEffect(() => {
    if (data && onEvent) onEvent(data)
  }, [data, onEvent])

  return { lastEvent: data, error }
}

/** 一条应用日志（字段与后端 `GET /api/logs` 和 SSE `log` 事件一致）。 */
export interface LogRecord {
  level: string
  logger: string
  event: string
  timestamp: string
}

/** 日志行数上限（面板是给人扫的，不是归档；再多也看不过来）。 */
const LOG_BUFFER = 300

/** 一行日志的去重键：断线重连会重放历史，同一行不能出现两次。 */
function lineKey(line: LogRecord): string {
  return `${line.timestamp}|${line.logger}|${line.event}`
}

/**
 * 订阅应用日志（`/api/stream/logs`，需要 `system:logs`）。
 *
 * 与领域事件是**两条流**，故意不合并：领域事件答"发生了什么业务动作"，
 * 应用日志答"服务端怎么想的"（排障才看）。
 *
 * 两条实现约束：
 *
 * 1. **按帧合并，不做逐条 setState** —— 服务端会先回放 `backlog` 条（默认 80），
 *    逐条 setState 会让几十次更新在同一轮里嵌套，直接撞上 React 的更新深度上限
 *    （`Maximum update depth exceeded`，#185，2026-10-11 实测复现）；
 * 2. **按「时间戳 + logger + 内容」去重** —— 重连会重放，不去重面板里会重复。
 */
export function useLogStream(options?: { minLevel?: string; backlog?: number; enabled?: boolean }) {
  const { minLevel = "info", backlog = 50, enabled = true } = options ?? {}
  const [lines, setLines] = useState<LogRecord[]>([])
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    if (!enabled) return
    const query = `min_level=${encodeURIComponent(minLevel)}&backlog=${backlog}`
    const source = new EventSource(`/api/stream/logs?${query}`)
    const pending: LogRecord[] = []
    let scheduled = false

    const flush = () => {
      scheduled = false
      const batch = pending.splice(0, pending.length)
      if (batch.length === 0) return
      setLines((previous) => {
        const seen = new Set(previous.map(lineKey))
        const fresh = batch.filter((line) => !seen.has(lineKey(line)))
        if (fresh.length === 0) return previous
        return [...previous, ...fresh].slice(-LOG_BUFFER)
      })
    }

    const handler: EventListener = (raw) => {
      const message = raw as MessageEvent<string>
      try {
        pending.push(JSON.parse(message.data) as LogRecord)
      } catch {
        return // 单条坏行不该断掉整条流
      }
      setError(null)
      // 同一轮里到达的多条（回放/突发）合成**一次**提交：逐条 setState 会嵌套成
      // `Maximum update depth exceeded`（#185，2026-10-11 实测）。用微任务而不是
      // requestAnimationFrame —— 后台标签页里 rAF 不触发，面板会一直是空的。
      if (!scheduled) {
        scheduled = true
        queueMicrotask(flush)
      }
    }
    const onError = () => setError(new Error("连接中断，浏览器会自动重连"))

    source.addEventListener("log", handler)
    source.addEventListener("error", onError)
    return () => {
      source.removeEventListener("log", handler)
      source.removeEventListener("error", onError)
      source.close()
    }
  }, [enabled, minLevel, backlog])

  const clear = useCallback(() => setLines([]), [])

  return { lines, error, clear }
}
