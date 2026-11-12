import useSWR from "swr"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { apiGet } from "@/lib/api"
import { type BadgeTone, errorText, PanelBody, SectionCard } from "./panel-state"

/** 一条探针结果（与 `GET /system/doctor` 一致）。 */
interface Probe {
  name: string
  status: string
  detail: string
}

/** 运行概览（与 `GET /system/overview` 一致）。 */
interface Overview {
  kpi: Record<string, number>
  bus: Record<string, number>
  answers: Record<string, number>
  accounts: number
  database: string
}

/** 探针结论：颜色只是辅助，文字才是结论本身。 */
const PROBE_STATUS: Record<string, { label: string; variant: BadgeTone }> = {
  PASS: { label: "通过", variant: "default" },
  WARN: { label: "警告", variant: "outline" },
  FAIL: { label: "失败", variant: "destructive" },
  SKIP: { label: "跳过", variant: "ghost" },
}

/**
 * 诊断面板：装配是否正确 + 进程正在做什么。
 *
 * 探针是**主动执行**的检查，所以给一个"重新体检"按钮；概览数字跟着刷新走，
 * 两者回答的是同一个问题（"服务在跑，但装对了吗"）。
 */
export function DoctorPanel() {
  const doctor = useSWR<Probe[]>("/system/doctor", (key: string) => apiGet<Probe[]>(key))
  const overview = useSWR<Overview>("/system/overview", (key: string) => apiGet<Overview>(key))
  const probes = doctor.data ?? []
  const failed = probes.filter((item) => item.status === "FAIL").length

  const facts = overview.data
    ? [
        ["题库总量", overview.data.answers.total],
        ["人工录入", overview.data.answers.manual],
        ["累计命中", overview.data.answers.hits],
        ["事件已发布", overview.data.bus.published],
        ["事件已处理", overview.data.bus.handled],
        ["事件出错", overview.data.bus.errors],
        ["事件丢弃", overview.data.bus.dropped],
        ["账号数", overview.data.accounts],
      ]
    : []

  return (
    <SectionCard
      title="诊断"
      description="端到端探针：容器装配 / 健康检查 / 静态托管 / SSE 端点，与 `cx doctor --full` 同一套探针。"
      headerRight={
        <Button
          size="sm"
          variant="outline"
          disabled={doctor.isValidating}
          onClick={() => void doctor.mutate()}
        >
          {doctor.isValidating ? "体检中…" : "重新体检"}
        </Button>
      }
    >
      <p className="text-sm">
        {doctor.data
          ? failed > 0
            ? `${probes.length} 项检查中有 ${failed} 项失败，先看红色那几条。`
            : `${probes.length} 项检查全部通过。`
          : "正在体检…"}
      </p>

      <PanelBody
        isLoading={doctor.isLoading}
        error={doctor.error}
        isEmpty={probes.length === 0}
        emptyText="探针没有返回任何结果，先确认服务进程是否正常。"
      >
        <ul className="divide-y">
          {probes.map((item) => {
            const meta = PROBE_STATUS[item.status]
            return (
              <li key={item.name} className="flex flex-col gap-1 py-3 sm:flex-row sm:gap-4">
                <div className="flex w-64 shrink-0 items-center gap-2">
                  <Badge variant={meta?.variant ?? "ghost"}>{meta?.label ?? item.status}</Badge>
                  <span className="truncate font-mono text-xs">{item.name}</span>
                </div>
                <p className="text-xs text-muted-foreground">{item.detail}</p>
              </li>
            )
          })}
        </ul>
      </PanelBody>

      <div className="flex flex-col gap-2 border-t pt-4">
        <h3 className="text-sm font-medium">运行概览</h3>
        {overview.error ? (
          <p className="text-sm text-destructive">读取失败：{errorText(overview.error)}</p>
        ) : overview.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : (
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
            {facts.map(([label, value]) => (
              <div key={String(label)} className="flex flex-col">
                <dt className="text-xs text-muted-foreground">{label}</dt>
                <dd className="font-mono text-sm">{value ?? "—"}</dd>
              </div>
            ))}
          </dl>
        )}
        <p className="truncate font-mono text-xs text-muted-foreground">
          数据库：{overview.data?.database ?? "—"}
        </p>
      </div>
    </SectionCard>
  )
}
