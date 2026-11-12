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
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { ApiError, apiDelete, apiGet, apiPatch, apiPost } from "@/lib/api"
import { usePerm } from "@/lib/auth"

/** 计划行（与 `/study/plans` 的响应同构）。 */
interface PlanRow {
  id: number
  name: string
  account_id: number
  enabled: boolean
  strategy: Record<string, unknown>
  course_ids: number[]
}

/** `/meta/enums` 的响应（前端不硬编码枚举）。 */
interface MetaEnums {
  strategy_keys: string[]
}

/**
 * 建计划 Dialog：名称 + 策略开关。
 *
 * 策略键来自 `/meta/enums.strategy_keys`，**前端不硬编码**；无既有计划作样本时
 * 只提交用户改动过的键，未动的键交给后端默认值合并。
 */
function PlanDialog({
  accountId,
  accountName,
  strategyKeys,
  sample,
  onClose,
  onCreated,
}: {
  accountId: number
  accountName: string
  strategyKeys: string[]
  sample: Record<string, unknown> | null
  onClose: () => void
  onCreated: () => void
}) {
  const [open, setOpen] = useState(true)
  const [name, setName] = useState(`${accountName} 计划`)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")

  // 只有样本里确实是布尔值的键才当开关（rate/order 这类数值/枚举保持后端默认）
  const boolKeys = sample
    ? strategyKeys.filter((key) => typeof sample[key] === "boolean")
    : strategyKeys
  const [values, setValues] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(
      boolKeys.map((key) => [
        key,
        typeof sample?.[key] === "boolean" ? (sample[key] as boolean) : true,
      ]),
    ),
  )
  const [touched, setTouched] = useState<Record<string, boolean>>({})

  async function submit() {
    setSubmitting(true)
    setError("")
    try {
      const strategy = Object.fromEntries(
        boolKeys.filter((key) => touched[key]).map((key) => [key, values[key]]),
      )
      await apiPost("/study/plans", {
        name: name.trim() || `${accountName} 计划`,
        account_id: accountId,
        course_keys: [], // 空 = 该账号已勾选的课程
        strategy,
      })
      onCreated()
      setOpen(false)
      onClose()
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "建计划失败")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (!next) onClose()
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>新建刷课计划</DialogTitle>
          <DialogDescription>课程集合 = 当前已勾选的课程；策略决定刷什么。</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <Label htmlFor="plan-name" className="text-xs">
              名称
            </Label>
            <Input id="plan-name" value={name} onChange={(event) => setName(event.target.value)} />
          </div>
          <div className="flex flex-col gap-2">
            <div className="text-xs text-muted-foreground">策略</div>
            {boolKeys.length === 0 ? (
              <p className="text-xs text-muted-foreground">没有可切换的策略，使用系统默认。</p>
            ) : (
              boolKeys.map((key) => (
                <div key={key} className="flex items-center justify-between gap-2">
                  <span className="text-sm">{key}</span>
                  <Switch
                    aria-label={`策略 ${key}`}
                    checked={values[key]}
                    onCheckedChange={(checked) => {
                      setValues((prev) => ({ ...prev, [key]: checked }))
                      setTouched((prev) => ({ ...prev, [key]: true }))
                    }}
                  />
                </div>
              ))
            )}
          </div>
          {error ? <p className="text-xs text-destructive">{error}</p> : null}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={submitting}>
            取消
          </Button>
          <Button onClick={() => void submit()} disabled={submitting}>
            {submitting ? "创建中…" : "创建"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/**
 * 刷课计划区：列出计划（启停 / 删除），并提供「用已勾选课程建计划」入口。
 *
 * 计划是"刷什么"的权威来源 —— 所以它不该是控制台里孤零零的一行按钮。
 */
export function PlansPanel({
  accountId,
  accountName,
  selectedCount,
}: {
  accountId: number | null
  accountName: string
  selectedCount: number
}) {
  const canRead = usePerm("study:plan:read")
  const canWrite = usePerm("study:plan:write")
  const {
    data: plans,
    error,
    isLoading,
    mutate,
  } = useSWR<PlanRow[]>(canRead && accountId ? ["/study/plans", accountId] : null, () =>
    apiGet<PlanRow[]>("/study/plans", { account_id: accountId }),
  )
  const { data: meta } = useSWR<MetaEnums>(canRead ? "/meta/enums" : null, (key: string) =>
    apiGet<MetaEnums>(key),
  )
  const [dialogOpen, setDialogOpen] = useState(false)
  const [message, setMessage] = useState("")

  if (!canRead) return null

  async function toggle(row: PlanRow) {
    setMessage("")
    try {
      await apiPatch(`/study/plans/${row.id}`, { enabled: !row.enabled })
      await mutate()
    } catch (cause) {
      setMessage(cause instanceof ApiError ? cause.message : "更新失败")
    }
  }

  async function remove(row: PlanRow) {
    setMessage("")
    try {
      await apiDelete(`/study/plans/${row.id}`)
      await mutate()
    } catch (cause) {
      setMessage(cause instanceof ApiError ? cause.message : "删除失败")
    }
  }

  const strategyKeys = meta?.strategy_keys ?? []
  const sample = plans && plans.length > 0 ? plans[0].strategy : null

  return (
    <Card>
      <CardHeader className="gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-base">刷课计划</CardTitle>
          {canWrite ? (
            <Button
              size="sm"
              variant="outline"
              disabled={!accountId}
              onClick={() => setDialogOpen(true)}
            >
              用已勾选课程建计划
            </Button>
          ) : null}
        </div>
        <CardDescription>
          计划决定「刷什么」；课程集合取当前已勾选的 {selectedCount} 门课。
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {!accountId ? (
          <p className="text-sm text-muted-foreground">先选择账号。</p>
        ) : isLoading ? (
          <p className="text-sm text-muted-foreground">加载中…</p>
        ) : error ? (
          <p className="text-sm text-destructive">计划加载失败：{String(error.message ?? error)}</p>
        ) : plans && plans.length === 0 ? (
          <p className="text-sm text-muted-foreground">还没有计划。</p>
        ) : null}

        {plans?.map((row) => (
          <div
            key={row.id}
            className="flex items-center justify-between gap-3 rounded-md border px-3 py-2"
          >
            <div className="min-w-0">
              <div className="truncate text-sm font-medium">{row.name}</div>
              <div className="text-xs text-muted-foreground">
                {row.course_ids.length} 门课 · {row.enabled ? "启用中" : "已停用"}
              </div>
            </div>
            {canWrite ? (
              <div className="flex shrink-0 items-center gap-2">
                <Switch
                  aria-label={`启用 ${row.name}`}
                  checked={row.enabled}
                  onCheckedChange={() => void toggle(row)}
                />
                <AlertDialog>
                  <AlertDialogTrigger render={<Button variant="destructive" size="sm" />}>
                    删除
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>删除计划？</AlertDialogTitle>
                      <AlertDialogDescription>
                        「{row.name}」将被删除，且不可恢复。
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>取消</AlertDialogCancel>
                      <AlertDialogAction variant="destructive" onClick={() => void remove(row)}>
                        删除
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              </div>
            ) : null}
          </div>
        ))}

        {message ? <p className="text-xs text-muted-foreground">{message}</p> : null}
      </CardContent>

      {dialogOpen && accountId ? (
        <PlanDialog
          accountId={accountId}
          accountName={accountName}
          strategyKeys={strategyKeys}
          sample={sample}
          onClose={() => setDialogOpen(false)}
          onCreated={() => void mutate()}
        />
      ) : null}
    </Card>
  )
}
