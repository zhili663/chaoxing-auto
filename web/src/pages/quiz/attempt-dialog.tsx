import type { ReactNode } from "react"

import { Badge } from "@/components/ui/badge"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Separator } from "@/components/ui/separator"

import { type AttemptRow, DETAIL_LABEL, formatStamp, questionText } from "./types"

/** 详情里的一行键值（左标签右值，长值换行而不是把 Dialog 撑破）。 */
function DetailRow({ name, children }: { name: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="shrink-0 text-muted-foreground">{name}</dt>
      <dd className="min-w-0 text-right break-all">{children}</dd>
    </div>
  )
}

/**
 * 一条交卷记录的明细。
 *
 * Dialog 而不是跳页：列表里是"我在找哪一次"，点开看一眼就该回到原位置。
 * 表里有的都摆出来 —— 题干指纹与答案来源**还没落这张表**（`detail` 里目前只有置信度），
 * 这里如实说明，而不是摆一个空字段装作有。
 */
export function AttemptDetailDialog({
  row,
  onOpenChange,
}: {
  row: AttemptRow | null
  onOpenChange: (open: boolean) => void
}) {
  if (!row) return null
  const entries = Object.entries(row.detail ?? {})

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>交卷详情</DialogTitle>
          <DialogDescription>
            {formatStamp(row.created_at)} · {row.account_label}
          </DialogDescription>
        </DialogHeader>
        <div className="flex max-h-[70vh] flex-col gap-3 overflow-y-auto pr-1 text-sm">
          <dl className="flex flex-col gap-2">
            <DetailRow name="测验作业 ID">
              <span className="font-mono text-xs">{row.work_id || "—"}</span>
            </DetailRow>
            <DetailRow name="题数（已答/总数）">{questionText(row)}</DetailRow>
            <DetailRow name="是否交卷">
              <Badge variant={row.submitted ? "secondary" : "outline"}>
                {row.submitted ? "已交卷" : "未交卷"}
              </Badge>
            </DetailRow>
            <DetailRow name="课程">{row.course_title || "未关联课程"}</DetailRow>
            <DetailRow name="任务点">
              {row.task_point_id === null ? "—" : `#${row.task_point_id}`}
            </DetailRow>
            <DetailRow name="服务端回执">{row.reply || "—"}</DetailRow>
          </dl>

          <Separator />

          <section className="flex flex-col gap-2">
            <h3 className="text-sm font-medium">作答详情</h3>
            {entries.length > 0 ? (
              <dl className="flex flex-col gap-2">
                {entries.map(([name, value]) => {
                  const text =
                    value === null || value === undefined
                      ? "—"
                      : typeof value === "object"
                        ? JSON.stringify(value)
                        : String(value)
                  return (
                    <DetailRow key={name} name={DETAIL_LABEL[name] ?? name}>
                      {text}
                    </DetailRow>
                  )
                })}
              </dl>
            ) : (
              <p className="text-sm text-muted-foreground">这条记录没有附加的作答详情</p>
            )}
            <p className="text-xs text-muted-foreground">
              题干指纹与答案来源目前不在这张表里，这一层留待下一步。
            </p>
          </section>
        </div>
      </DialogContent>
    </Dialog>
  )
}
