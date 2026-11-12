import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import type { TaskPoint } from "@/lib/hooks"

import { CaptchaDialog } from "@/pages/courses/captcha-dialog"

type BadgeVariant = "default" | "secondary" | "destructive" | "outline"

/** 任务点状态 → 中文与徽章样式（状态码来自后端，这里只负责展示）。 */
const STATUS: Record<string, { label: string; variant: BadgeVariant }> = {
  queued: { label: "排队中", variant: "outline" },
  running: { label: "进行中", variant: "secondary" },
  done: { label: "已完成", variant: "default" },
  failed: { label: "失败", variant: "destructive" },
  skipped: { label: "已跳过", variant: "outline" },
  need_human: { label: "待人工", variant: "destructive" },
}

function statusMeta(status: string): { label: string; variant: BadgeVariant } {
  return STATUS[status] ?? { label: status, variant: "outline" }
}

function percentOf(point: TaskPoint): number {
  return point.duration > 0 ? (point.progress / point.duration) * 100 : 0
}

/** 任务点明细：状态徽章 + 进度 + 失败原因（诊断信息随身带，不用再点开）。 */
export function PointsTable({
  points,
  onRefresh,
}: {
  points: TaskPoint[]
  onRefresh?: () => void
}) {
  const [captchaPoint, setCaptchaPoint] = useState<TaskPoint | null>(null)

  if (points.length === 0) {
    return (
      <p className="rounded-md border p-4 text-sm text-muted-foreground">
        这门课还没有任务点，先点「扫描任务点」。
      </p>
    )
  }

  return (
    <>
      <div className="hidden overflow-x-auto rounded-md border md:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-24">状态</TableHead>
              <TableHead>任务点</TableHead>
              <TableHead className="w-44">进度</TableHead>
              <TableHead className="w-28">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {points.map((point) => {
              const meta = statusMeta(point.status)
              return (
                <TableRow key={point.id}>
                  <TableCell>
                    <Badge variant={meta.variant}>{meta.label}</Badge>
                  </TableCell>
                  <TableCell className="max-w-0">
                    <div className="truncate text-sm">{point.title || point.point_id}</div>
                    <div className="truncate text-xs text-muted-foreground">
                      {point.kind}
                      {point.last_error ? (
                        <span className="text-destructive"> · {point.last_error}</span>
                      ) : null}
                    </div>
                  </TableCell>
                  <TableCell className="w-44">
                    <Progress value={percentOf(point)} />
                    <div className="mt-1 text-xs text-muted-foreground">
                      {point.progress}/{point.duration}
                    </div>
                  </TableCell>
                  <TableCell>
                    {point.status === "need_human" ? (
                      <Button size="sm" variant="outline" onClick={() => setCaptchaPoint(point)}>
                        验证码
                      </Button>
                    ) : null}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </div>

      {/* 窄屏：单列紧凑列表，避免横向滚动 */}
      <ul className="flex flex-col gap-2 md:hidden">
        {points.map((point) => {
          const meta = statusMeta(point.status)
          return (
            <li key={point.id} className="rounded-md border p-3">
              <div className="flex items-start justify-between gap-2">
                <span className="min-w-0 truncate text-sm">{point.title || point.point_id}</span>
                <Badge variant={meta.variant}>{meta.label}</Badge>
              </div>
              <div className="mt-1 truncate text-xs text-muted-foreground">{point.kind}</div>
              <Progress className="mt-2" value={percentOf(point)} />
              <div className="mt-1 text-xs text-muted-foreground">
                {point.progress}/{point.duration}
                {point.last_error ? (
                  <span className="text-destructive"> · {point.last_error}</span>
                ) : null}
              </div>
              {point.status === "need_human" ? (
                <Button
                  className="mt-2"
                  size="sm"
                  variant="outline"
                  onClick={() => setCaptchaPoint(point)}
                >
                  输入验证码
                </Button>
              ) : null}
            </li>
          )
        })}
      </ul>

      {captchaPoint ? (
        <CaptchaDialog
          pointId={captchaPoint.id}
          pointTitle={captchaPoint.title}
          onClose={() => setCaptchaPoint(null)}
          onResolved={() => onRefresh?.()}
        />
      ) : null}
    </>
  )
}
