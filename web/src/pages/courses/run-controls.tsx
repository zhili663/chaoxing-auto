import { useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ApiError } from "@/lib/api"
import { usePerm } from "@/lib/auth"
import { type DomainEvent, useEventStream } from "@/lib/events"
import { pauseStudy, resumeStudy, useStudyRuns } from "@/lib/hooks"

/**
 * 控制状态 → 徽章（文案与后端 `study_control.STATE_NOTES` 的语义一一对应）。
 *
 * `paused` 是终点态：运行器已经在检查点交出控制权，**本次到此为止**——
 * 所以"继续"是重新开跑，而不是解除某个阻塞。
 */
const LABELS: Record<string, { text: string; variant: "outline" | "secondary" | "destructive" }> = {
  running: { text: "运行中", variant: "outline" },
  pause_requested: { text: "正在暂停…", variant: "secondary" },
  paused: { text: "已暂停", variant: "secondary" },
  resume_requested: { text: "正在继续…", variant: "secondary" },
  stop_requested: { text: "正在中止…", variant: "destructive" },
}

/** 会让控制状态变动的领域事件（进度事件也要监听：暂停正是在心跳那一刻生效的）。 */
const LIVE_EVENTS: Record<string, true> = {
  TaskPointStarted: true,
  TaskPointProgress: true,
  TaskPointFinished: true,
  StudyRunFinished: true,
}

/** 这些状态下没有"暂停"可点：已经在暂停态，或正在中止。 */
const PAUSE_UNAVAILABLE: Record<string, true> = {
  pause_requested: true,
  paused: true,
  stop_requested: true,
}

interface ActionState {
  state: string
}

/**
 * 刷课控制：暂停 / 继续与状态徽章。
 *
 * 三条设计要点：
 *
 * 1. **没有定时器** —— 状态来自 SSE 事件触发的重新拉取，外加用户显式点的"刷新"；
 * 2. 状态与按钮的可用性都由 `state` 决定（例如"运行中"时没有暂停可继续），
 *    避免用户点出一个必然 409 的请求；
 * 3. 无 `study:plan:control` 权限时按钮**不渲染**并说明原因，而不是渲染成禁用态。
 */
export function RunControls({ accountId }: { accountId: number | null }) {
  const canControl = usePerm("study:plan:control")
  const { data: runs, mutate } = useStudyRuns()
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState("")

  useEventStream((event: DomainEvent) => {
    if (LIVE_EVENTS[event.type]) void mutate()
  })

  if (accountId === null) return null

  const run = runs?.find((item) => item.account_id === accountId) ?? null
  const state = run?.state ?? "running"
  const label = LABELS[state] ?? { text: state, variant: "outline" as const }

  async function act(
    verb: string,
    action: (accountId: number) => Promise<ActionState>,
  ): Promise<void> {
    if (accountId === null) return
    setBusy(verb)
    setMessage("")
    try {
      const next = await action(accountId)
      await mutate()
      setMessage(`${verb}请求已提交（${next.state}）—— 运行器会在下一个检查点生效`)
    } catch (cause) {
      setMessage(cause instanceof ApiError ? cause.message : `${verb}失败`)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border px-3 py-2">
      <span className="text-xs text-muted-foreground">刷课控制</span>
      <Badge variant={label.variant} title={run?.note ?? ""}>
        {busy ? `${busy}中…` : label.text}
      </Badge>
      {canControl ? (
        <>
          <Button
            size="sm"
            variant="outline"
            disabled={busy !== null || PAUSE_UNAVAILABLE[state] === true}
            onClick={() => void act("暂停", pauseStudy)}
          >
            暂停
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy !== null || state === "running"}
            onClick={() => void act("继续", resumeStudy)}
          >
            继续
          </Button>
        </>
      ) : (
        <span className="text-xs text-muted-foreground">
          当前账号没有 study:plan:control 权限，只能在「刷课计划」页运行任务
        </span>
      )}
      <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => void mutate()}>
        刷新
      </Button>
      {run?.note ? <span className="text-xs text-muted-foreground">{run.note}</span> : null}
      {message ? <span className="text-xs">{message}</span> : null}
    </div>
  )
}
