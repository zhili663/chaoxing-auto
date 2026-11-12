import { useEffect, useMemo, useState } from "react"
import { Link } from "react-router-dom"
import useSWR from "swr"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { apiGet } from "@/lib/api"
import { usePerm } from "@/lib/auth"
import { type DomainEvent, type LogRecord, useLogStream } from "@/lib/events"
import { type ActiveRuns, useActiveRuns } from "@/lib/hooks"

/** KPI 形状（与后端 `/stats/kpi` 一致）。 */
export interface Kpi {
  courses: number
  selected_courses: number
  points_total: number
  points_done: number
  points_failed: number
  points_need_human: number
  points_queued: number
  watched_minutes: number
  quizzes_done: number
}

/** 一天的活动（与后端 `/stats/daily` 一致）。 */
export interface DailyPoint {
  day: string
  points_done: number
  quiz_done: number
  watched_minutes: number
  failed_count: number
}

const KPI_CARDS: Array<{ key: keyof Kpi; label: string; hint: string; unit?: string }> = [
  { key: "points_done", label: "已完成任务点", hint: "累计", unit: "个" },
  { key: "points_queued", label: "待刷", hint: "计划里还剩多少", unit: "个" },
  { key: "watched_minutes", label: "累计学习", hint: "视频时长折算", unit: "分钟" },
  { key: "quizzes_done", label: "完成测验", hint: "含随堂", unit: "次" },
]

const SWR_OPTS = { shouldRetryOnError: false, revalidateOnFocus: false }

/** 热力等级：把完成量映射成 0~4 档（不依赖色阶库，够用且稳）。 */
export function heatLevel(points: number, scale: number): number {
  if (points <= 0 || scale <= 0) return 0
  return Math.min(4, Math.max(1, Math.round((points / scale) * 4)))
}

const HEAT_CLASS = [
  "bg-muted",
  "bg-emerald-200 dark:bg-emerald-900",
  "bg-emerald-300 dark:bg-emerald-700",
  "bg-emerald-400 dark:bg-emerald-600",
  "bg-emerald-600 dark:bg-emerald-400",
]

const LOG_LEVELS = [
  { value: "info", label: "全部" },
  { value: "warning", label: "警告以上" },
  { value: "error", label: "仅错误" },
]

const LEVEL_CLASS: Record<string, string> = {
  error: "text-destructive",
  exception: "text-destructive",
  warning: "text-amber-600",
  info: "text-muted-foreground",
}

/** KPI 四卡：加载给骨架、出错说清原因（403 要能看出缺哪个权限），不要用 0 冒充数据。 */
function KpiCards({ kpi, error, isLoading }: { kpi?: Kpi; error?: Error; isLoading: boolean }) {
  if (error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>指标暂时取不到</CardTitle>
          <CardDescription>{error.message}</CardDescription>
        </CardHeader>
      </Card>
    )
  }
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {KPI_CARDS.map((item) => (
        <Card key={item.key}>
          <CardHeader className="pb-1">
            <CardDescription>{item.label}</CardDescription>
            {isLoading ? (
              <Skeleton className="h-9 w-20" />
            ) : (
              <CardTitle className="text-3xl tabular-nums">
                {kpi?.[item.key] ?? "—"}
                {item.unit && kpi ? (
                  <span className="ml-1 text-sm font-normal text-muted-foreground">
                    {item.unit}
                  </span>
                ) : null}
              </CardTitle>
            )}
          </CardHeader>
          <CardContent className="text-xs text-muted-foreground">{item.hint}</CardContent>
        </Card>
      ))}
    </div>
  )
}

/** 任务点类型 → 展示名（只做展示，不参与业务判断）。 */
const KIND_LABELS: Record<string, string> = {
  video: "视频",
  quiz: "测验",
  exam: "考试",
  document: "文档",
  read: "阅读",
  discussion: "讨论",
}

function formatStarted(value?: string | null): string {
  if (!value) return "—"
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString("zh-CN", { hour12: false })
}

/**
 * 「并发刷课」模块：并发模式常开，展示此刻正在跑的账号与当前任务点。
 *
 * 数据来自 `/study/active`（`study_sessions.status='running'` + 当前 running 任务点），
 * 不按账号切换 —— 这里回答的是"现在系统里有哪些号在跑"。
 */
function ActiveRunsCard({
  data,
  error,
  isLoading,
}: {
  data?: ActiveRuns
  error?: Error
  isLoading: boolean
}) {
  const accounts = data?.accounts ?? []
  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle>并发刷课</CardTitle>
          <Badge variant="secondary">并发模式 · 常开</Badge>
          {data ? (
            <Badge variant="outline" className="tabular-nums">
              {accounts.length} 个账号在跑
            </Badge>
          ) : null}
          <span className="ml-auto text-xs text-muted-foreground">
            并发上限 {data?.concurrency ?? "—"}
          </span>
        </div>
        <CardDescription>
          当前正在刷课的账号（每账号独立会话、独立协程；这里只做展示，不提供开关）
        </CardDescription>
      </CardHeader>
      <CardContent>
        {error ? (
          <p className="text-sm text-destructive">活跃账号暂时取不到：{error.message}</p>
        ) : isLoading ? (
          <div className="flex flex-col gap-2">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : accounts.length === 0 ? (
          <p className="rounded-md border p-4 text-sm text-muted-foreground">
            当前没有账号在刷课。并发模式已开启 —— 到课程页启动任务后，这里会显示正在跑的账号。
          </p>
        ) : (
          <ul className="flex flex-col divide-y">
            {accounts.map((item) => {
              const percent =
                item.duration > 0 ? Math.min(100, (item.progress / item.duration) * 100) : null
              return (
                <li
                  key={item.account_id}
                  className="flex items-center gap-3 py-2 first:pt-0 last:pb-0"
                >
                  <span className="size-2 shrink-0 rounded-full bg-emerald-500" aria-hidden />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="truncate text-sm font-medium">{item.username}</span>
                      {item.kind ? (
                        <Badge variant="outline" className="shrink-0">
                          {KIND_LABELS[item.kind] ?? item.kind}
                        </Badge>
                      ) : null}
                    </div>
                    <div className="truncate text-xs text-muted-foreground">
                      {item.point_title || "等待下一个任务点"}
                    </div>
                    {percent !== null ? <Progress value={percent} className="mt-1 h-1.5" /> : null}
                  </div>
                  <div className="shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                    {item.duration > 0
                      ? `${Math.floor(item.progress)}/${Math.floor(item.duration)}s`
                      : ""}
                    <div>开始 {formatStarted(item.started_at)}</div>
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

/** 13 周热力图：空数据时说明"为什么是空的"，而不是留一片灰格子。 */
function Heatmap({ series, isLoading }: { series: DailyPoint[]; isLoading: boolean }) {
  const scale = series.reduce((max, item) => Math.max(max, item.points_done), 0)
  const empty = !isLoading && scale === 0
  return (
    <Card>
      <CardHeader>
        <CardTitle>近 13 周</CardTitle>
        <CardDescription>
          {empty ? "这段时间还没有完成记录 —— 先在课程页跑一次扫描 / 刷课" : "按天累计的完成任务点"}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          <div className="overflow-x-auto">
            <div className="grid grid-flow-col grid-rows-7 gap-1">
              {series.map((item) => (
                <div
                  key={item.day}
                  title={`${item.day}：完成 ${item.points_done} · 测验 ${item.quiz_done} · ${item.watched_minutes} 分钟`}
                  className={`size-3 rounded-sm ${HEAT_CLASS[heatLevel(item.points_done, scale)]}`}
                />
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function LogLines({ lines, empty }: { lines: LogRecord[]; empty: string }) {
  if (lines.length === 0) {
    return <div className="p-3 text-xs text-muted-foreground">{empty}</div>
  }
  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-2 font-mono text-xs leading-relaxed">
      {lines.map((line) => (
        <div
          key={`${line.timestamp}|${line.logger}|${line.event}`}
          className={LEVEL_CLASS[line.level] ?? ""}
        >
          <span className="opacity-60">
            {new Date(line.timestamp).toLocaleTimeString("zh-CN", { hour12: false })}
          </span>{" "}
          <span className="opacity-80">[{line.logger.split(".").pop()}]</span> {line.event}
        </div>
      ))}
    </div>
  )
}

/**
 * 应用日志面板（`system:logs`）：服务端在"想什么"。
 *
 * 与领域事件刻意分开：这块是排障用的，只有管理员看得到；普通用户的实时反馈在课程页。
 * 窄屏不塞一个固定高度的小框，改成打开整屏 Sheet。
 */
function LogPanel() {
  const allowed = usePerm("system:logs")
  const [level, setLevel] = useState("info")
  // 受控值必须是**稳定引用**：每次渲染新建 `[level]` 会让 ToggleGroup 内部同步 → setState → 死循环
  const levelValue = useMemo(() => [level], [level])
  const { lines, error, clear } = useLogStream({
    minLevel: level,
    enabled: allowed,
    backlog: 80,
  })

  if (!allowed) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>应用日志</CardTitle>
          <CardDescription>需要 `system:logs` 权限（管理员可见）</CardDescription>
        </CardHeader>
      </Card>
    )
  }

  const body = (
    <>
      <div className="flex flex-wrap items-center gap-2 border-b px-2 py-1.5">
        <ToggleGroup
          value={levelValue}
          onValueChange={(next: string[]) => {
            const picked = next[0]
            if (picked && picked !== level) setLevel(picked)
          }}
          size="sm"
        >
          {LOG_LEVELS.map((item) => (
            <ToggleGroupItem key={item.value} value={item.value}>
              {item.label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <Badge variant="outline" className="ml-auto font-mono text-[10px]">
          SSE
        </Badge>
        <Button variant="ghost" size="sm" onClick={clear}>
          清空
        </Button>
      </div>
      {error ? (
        <div className="p-3 text-xs text-destructive">日志流断开：{error.message}</div>
      ) : null}
      <LogLines lines={lines} empty="等待服务端日志…（有请求或刷课动作时就会动）" />
    </>
  )

  return (
    <Card className="flex min-h-0 flex-col">
      <CardHeader className="pb-2">
        <CardTitle>应用日志</CardTitle>
        <CardDescription>
          服务端实时日志（SSE 推送，无轮询）；排障用，不落在这个页面之外
        </CardDescription>
      </CardHeader>
      <CardContent className="hidden min-h-0 flex-1 flex-col p-0 lg:flex">
        <div className="flex h-64 flex-col">{body}</div>
      </CardContent>
      <CardContent className="p-0 lg:hidden">
        <Sheet>
          <SheetTrigger render={<Button variant="outline" className="m-3" />}>
            查看日志（{lines.length}）
          </SheetTrigger>
          <SheetContent side="right" className="flex w-full flex-col sm:max-w-lg">
            <SheetHeader>
              <SheetTitle>应用日志</SheetTitle>
            </SheetHeader>
            <div className="flex min-h-0 flex-1 flex-col">{body}</div>
          </SheetContent>
        </Sheet>
      </CardContent>
    </Card>
  )
}

/**
 * 仪表盘：KPI + 学习热力图 + 应用日志。
 *
 * 数据范围由后端决定：`/stats/kpi` 不指定账号时要求 `account:read:all`（全局视角），
 * 普通用户拿到的是"请先选账号"，所以这里按权限切 key 而不是靠前端假装看得到。
 */
export function DashboardPage({ lastEvent }: { lastEvent?: DomainEvent }) {
  const canSeeAll = usePerm("account:read:all")
  const {
    data: activeRuns,
    error: activeError,
    isLoading: activeLoading,
    mutate: mutateActive,
  } = useActiveRuns(canSeeAll)
  const {
    data: kpi,
    error: kpiError,
    isLoading: kpiLoading,
    mutate: mutateKpi,
  } = useSWR<Kpi>(canSeeAll ? "/stats/kpi" : null, (path: string) => apiGet<Kpi>(path), SWR_OPTS)
  const {
    data: daily,
    isLoading: dailyLoading,
    mutate: mutateDaily,
  } = useSWR<DailyPoint[]>(canSeeAll ? "/stats/daily?days=91" : null, (path: string) =>
    apiGet<DailyPoint[]>(path),
  )

  // SSE 到达时立刻刷新；活跃账号卡片另有 3s 轮询兜底（跑批可能在 CLI 进程，
  // 它的 SSE 不会进入 API 进程的事件流）。依赖里只放 `lastEvent` —— mutate 的引用
  // 由 SWR 保证稳定，但它变了会重跑 effect（#185 的经典坑）。
  useEffect(() => {
    if (!lastEvent) return
    void mutateKpi()
    void mutateDaily()
    void mutateActive()
  }, [lastEvent, mutateKpi, mutateDaily, mutateActive])

  if (!canSeeAll) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>请到课程页按账号查看</CardTitle>
          <CardDescription>
            你的数据范围是「自己的账号」，仪表盘只显示全局视角；账号维度的进度在课程页里。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button render={<Link to="/courses" />}>去课程页</Button>
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <KpiCards kpi={kpi} error={kpiError} isLoading={kpiLoading} />
      <ActiveRunsCard data={activeRuns} error={activeError} isLoading={activeLoading} />
      <Heatmap series={daily ?? []} isLoading={dailyLoading} />
      <LogPanel />
    </div>
  )
}
