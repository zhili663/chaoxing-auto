import { useState } from "react"
import useSWR from "swr"
import { Button } from "@/components/ui/button"
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { apiGet } from "@/lib/api"
import { usePerm } from "@/lib/auth"
import { PromptsSection } from "./settings-prompts"
import { SettingRow } from "./settings-row"
import { errorText, type SettingItem, type SettingsMap } from "./settings-specs"

/**
 * 设置页：只放**全局默认值**。
 *
 * 账号、课程、用户级别的参数留在各自的详情页里（见 UX.md §Settings ergonomics）——
 * 设置页是"全局开关台"，不是垃圾桶。改动写进数据库覆盖层，立即生效、重启不丢。
 */
export function SettingsPage() {
  const canRead = usePerm("settings:read")
  const canWrite = usePerm("settings:write")
  const canWritePrompt = usePerm("prompt:write")
  const { data, error, isLoading, mutate } = useSWR<SettingsMap>(
    canRead ? "/settings" : null,
    (key: string) => apiGet<SettingsMap>(key),
  )

  if (!canRead) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">没有权限</CardTitle>
          <CardDescription>
            全局配置需要 `settings:read` 节点；个人数据在仪表盘与账号页里，不需要这里。
          </CardDescription>
        </CardHeader>
      </Card>
    )
  }

  const entries = Object.entries(data ?? {})
  const model = entries.filter(([key]) => key.startsWith("ai."))
  const ui = entries.filter(([key]) => key.startsWith("obs."))
  const advanced = entries.filter(([key]) => !key.startsWith("ai.") && !key.startsWith("obs."))

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-1">
        <h1 className="font-heading text-lg font-medium">设置</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          来源三层：代码默认 → 环境变量 → 数据库覆盖。这里改的是最上面那层，改完立即生效、重启不丢；
          账号、课程、用户各自的参数在它们自己的页面里调。
        </p>
      </header>

      {error ? <p className="text-sm text-destructive">读取失败：{errorText(error)}</p> : null}
      {isLoading ? <Skeleton className="h-56 w-full" /> : null}

      <Section
        title="模型"
        description="答题用的大模型端点，下一道题生效。"
        entries={model}
        canWrite={canWrite}
        onSaved={mutate}
      />

      <PromptsSection canWrite={canWritePrompt} />

      <Section
        title="界面"
        description="界面与可观测性相关的项：日志级别决定落盘粒度，事件缓冲决定断线重连能补多久。"
        entries={ui}
        canWrite={canWrite}
        onSaved={mutate}
      />

      <AdvancedSection entries={advanced} canWrite={canWrite} onSaved={mutate} />
    </div>
  )
}

/** 一个配置分组：一行一项，用分隔线而不是卡片堆（见 UX.md §Avoid card soup）。 */
function Section({
  title,
  description,
  entries,
  canWrite,
  onSaved,
}: {
  title: string
  description: string
  entries: [string, SettingItem][]
  canWrite: boolean
  onSaved: () => Promise<unknown>
}) {
  if (entries.length === 0) return null
  return (
    <section className="flex flex-col gap-1">
      <div className="flex flex-col gap-0.5">
        <h2 className="font-heading text-sm font-medium">{title}</h2>
        <p className="text-xs text-muted-foreground">{description}</p>
      </div>
      <div className="divide-y">
        {entries.map(([key, item]) => (
          <SettingRow
            key={key}
            settingKey={key}
            item={item}
            canWrite={canWrite}
            onSaved={onSaved}
          />
        ))}
      </div>
    </section>
  )
}

/** 高级项（刷课节奏、限速、并发）：改错直接影响账号安全，默认折叠。 */
function AdvancedSection({
  entries,
  canWrite,
  onSaved,
}: {
  entries: [string, SettingItem][]
  canWrite: boolean
  onSaved: () => Promise<unknown>
}) {
  const [open, setOpen] = useState(false)
  if (entries.length === 0) return null
  return (
    <section className="flex flex-col gap-1">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-0.5">
          <h2 className="font-heading text-sm font-medium">高级</h2>
          <p className="text-xs text-muted-foreground">
            超星侧的节奏、限速与并发：偏保守才安全，不确定就别动。
          </p>
        </div>
        <Button size="sm" variant="outline" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "收起" : `展开 ${entries.length} 项`}
        </Button>
      </div>
      {open ? (
        <div className="divide-y">
          {entries.map(([key, item]) => (
            <SettingRow
              key={key}
              settingKey={key}
              item={item}
              canWrite={canWrite}
              onSaved={onSaved}
            />
          ))}
        </div>
      ) : null}
    </section>
  )
}
