import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { apiDelete, apiPatch } from "@/lib/api"
import { SettingControl } from "./settings-controls"
import { errorText, isNumericScope, type SettingItem, SPECS } from "./settings-specs"

/**
 * 一行可调项：左侧是语义（标签、说明、来源），右侧是唯一的控件。
 *
 * 改完即时保存（PATCH `/settings/{key}`），反馈贴在控件下面 —— 设置页要回答的是
 * "这一项现在到底生效了没有"，而不是"整页提交成功"。改动全部写进数据库覆盖层。
 */
export function SettingRow({
  settingKey,
  item,
  canWrite,
  onSaved,
}: {
  settingKey: string
  item: SettingItem
  canWrite: boolean
  onSaved: () => Promise<unknown>
}) {
  const spec = SPECS[settingKey]
  const id = `setting-${settingKey.replaceAll(".", "-")}`
  const [draft, setDraft] = useState<string | null>(null)
  const [sent, setSent] = useState(false)
  const [feedback, setFeedback] = useState<{ error: boolean; text: string } | null>(null)

  async function save(next: unknown) {
    setSent(true)
    setFeedback(null)
    try {
      await apiPatch(`/settings/${encodeURIComponent(settingKey)}`, { value: next })
      await onSaved()
      setDraft(null)
      setFeedback({ error: false, text: "已保存" })
    } catch (cause) {
      setFeedback({ error: true, text: errorText(cause) })
    } finally {
      setSent(false)
    }
  }

  /** 文本框/数字框的提交口：数字先本地校验边界，再交给后端做权威校验。 */
  async function commitDraft() {
    if (draft === null) return
    if (isNumericScope(item, spec)) {
      const parsed = Number(draft.trim())
      if (draft.trim() === "" || !Number.isFinite(parsed)) {
        setFeedback({ error: true, text: "需要一个数字" })
        return
      }
      const bounds = spec ?? {}
      if (
        (bounds.min !== undefined && parsed < bounds.min) ||
        (bounds.max !== undefined && parsed > bounds.max)
      ) {
        setFeedback({
          error: true,
          text: `需要 ${bounds.min ?? "-∞"}–${bounds.max ?? "+∞"} 之间的数字`,
        })
        return
      }
      await save(parsed)
      return
    }
    await save(draft)
  }

  async function restore() {
    setSent(true)
    setFeedback(null)
    try {
      await apiDelete(`/settings/${encodeURIComponent(settingKey)}`)
      await onSaved()
      setDraft(null)
      setFeedback({ error: false, text: "已回到默认值" })
    } catch (cause) {
      setFeedback({ error: true, text: errorText(cause) })
    } finally {
      setSent(false)
    }
  }

  return (
    <div className="grid gap-2 py-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,20rem)] sm:items-start sm:gap-6">
      <div className="flex flex-col gap-1">
        <Label id={`${id}-label`} htmlFor={id} className="w-fit">
          {spec?.label ?? settingKey}
        </Label>
        <p className="text-xs leading-snug text-muted-foreground">{spec?.hint ?? "没有说明"}</p>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={item.overridden ? "secondary" : "outline"}>
            {item.overridden ? "数据库覆盖" : "环境变量或默认"}
          </Badge>
          <code className="font-mono text-[11px] text-muted-foreground">{settingKey}</code>
          {item.overridden && canWrite ? (
            <Button
              size="sm"
              variant="ghost"
              className="h-5 px-1.5 text-xs"
              disabled={sent}
              onClick={() => void restore()}
            >
              恢复默认
            </Button>
          ) : null}
        </div>
      </div>
      <div className="flex flex-col gap-1.5">
        <SettingControl
          id={id}
          settingKey={settingKey}
          item={item}
          spec={spec}
          canWrite={canWrite}
          draft={draft}
          sent={sent}
          onDraft={setDraft}
          onCommitDraft={() => void commitDraft()}
          onCommitValue={(next) => void save(next)}
        />
        <p
          role={feedback?.error ? "alert" : "status"}
          className={feedback?.error ? "text-xs text-destructive" : "text-xs text-muted-foreground"}
        >
          {feedback?.text ?? (canWrite ? "" : "需要 settings:write 权限")}
        </p>
      </div>
    </div>
  )
}
