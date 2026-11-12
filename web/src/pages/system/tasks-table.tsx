import { useState } from "react"
import useSWR from "swr"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { ApiError, apiGet, apiPost } from "@/lib/api"
import { type BadgeTone, errorText, PanelBody, SectionCard, stamp } from "./panel-state"

/** 一条任务记录（与 `GET /system/tasks` 一致）。 */
interface TaskRow {
  id: number
  kind: string
  account_id: number | null
  status: string
  result: string
  started_at: string | null
  finished_at: string | null
}

const TASK_STATUS: Record<string, { label: string; variant: BadgeTone }> = {
  queued: { label: "排队中", variant: "outline" },
  running: { label: "运行中", variant: "default" },
  done: { label: "已完成", variant: "secondary" },
  failed: { label: "失败", variant: "destructive" },
  cancelled: { label: "已取消", variant: "ghost" },
}

/** 可取消的状态（与后端 `JobService.CANCELLABLE` 同一口径）。 */
const CANCELLABLE = ["queued", "running"]

/** 任务队列：最近 100 条，能做的事只有一件 —— 把卡住的那个取消掉。 */
export function TasksTable() {
  const { data, error, isLoading, mutate } = useSWR<TaskRow[]>(
    "/system/tasks?limit=100",
    (key: string) => apiGet<TaskRow[]>(key),
  )
  const [target, setTarget] = useState<TaskRow | null>(null)
  const [feedback, setFeedback] = useState<{ error: boolean; text: string } | null>(null)
  const rows = data ?? []

  async function cancel(task: TaskRow) {
    setFeedback(null)
    try {
      await apiPost(`/system/tasks/${task.id}/cancel`)
      await mutate()
      setFeedback({ error: false, text: `任务 #${task.id} 已取消` })
    } catch (cause) {
      // 点开确认框和目标真被提交之间状态可能已经变了（后端回 400）：
      // 这时刷新列表比复述错误更有用，但也要把原因说清楚。
      const stale = cause instanceof ApiError && cause.code === "job_not_cancellable"
      setFeedback({
        error: true,
        text: stale ? "任务状态已经变了（多半刚结束），列表已刷新" : errorText(cause),
      })
      await mutate()
    } finally {
      setTarget(null)
    }
  }

  return (
    <SectionCard
      title="任务队列"
      description="最近的 100 条手动任务。只有排队中 / 运行中的任务能取消 —— 其余状态取消没有意义，后端会直接拒绝。"
      headerRight={
        <Button size="sm" variant="outline" disabled={isLoading} onClick={() => void mutate()}>
          刷新
        </Button>
      }
    >
      {feedback ? (
        <p
          role={feedback.error ? "alert" : "status"}
          className={feedback.error ? "text-sm text-destructive" : "text-sm text-muted-foreground"}
        >
          {feedback.text}
        </p>
      ) : null}

      <PanelBody
        isLoading={isLoading}
        error={error}
        isEmpty={rows.length === 0}
        emptyText="还没有任务记录 —— 从课程页发起一次刷课，这里就会出现条目。"
      >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-16">编号</TableHead>
              <TableHead>类型</TableHead>
              <TableHead className="w-20">账号</TableHead>
              <TableHead className="w-24">状态</TableHead>
              <TableHead className="w-40">开始</TableHead>
              <TableHead className="w-40">结束</TableHead>
              <TableHead className="w-56">结果</TableHead>
              <TableHead className="w-24" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((task) => {
              const meta = TASK_STATUS[task.status]
              return (
                <TableRow key={task.id}>
                  <TableCell className="font-mono text-xs">#{task.id}</TableCell>
                  <TableCell className="text-xs">{task.kind}</TableCell>
                  <TableCell className="text-xs">{task.account_id ?? "—"}</TableCell>
                  <TableCell>
                    <Badge variant={meta?.variant ?? "ghost"}>{meta?.label ?? task.status}</Badge>
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {stamp(task.started_at)}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {stamp(task.finished_at)}
                  </TableCell>
                  <TableCell className="max-w-0">
                    <span
                      className="block truncate text-xs text-muted-foreground"
                      title={task.result}
                    >
                      {task.result || "—"}
                    </span>
                  </TableCell>
                  <TableCell>
                    {CANCELLABLE.includes(task.status) ? (
                      <Button size="sm" variant="destructive" onClick={() => setTarget(task)}>
                        取消
                      </Button>
                    ) : null}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </PanelBody>

      <AlertDialog
        open={target !== null}
        onOpenChange={(open) => {
          if (!open) setTarget(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>取消任务 #{target?.id}？</AlertDialogTitle>
            <AlertDialogDescription>
              运行中的任务会在下一个检查点停下，已经刷完的进度不会回滚；如果状态刚刚变成结束，后端会拒绝这次取消。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>不取消</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                if (target) void cancel(target)
              }}
            >
              确认取消
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </SectionCard>
  )
}
