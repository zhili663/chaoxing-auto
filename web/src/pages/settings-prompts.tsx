import { useState } from "react"
import useSWR from "swr"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { apiGet, apiPut } from "@/lib/api"
import { usePerm } from "@/lib/auth"
import { errorText } from "./settings-specs"

/** 提示词（与 `GET /settings/prompts` 的响应同构）。 */
interface PromptRow {
  key: string
  content: string
  version: number
  description: string
}

/**
 * 提示词：存数据库、改完即生效（不需要重启）。
 *
 * 服务端唯一的变更痕迹是版本号（每次保存 +1），所以卡片上把它摆在最显眼处，
 * 用来核对"这轮答案用的是哪一版"。更新时间后端没提供，只能显示本会话的保存时刻。
 */
export function PromptsSection({ canWrite }: { canWrite: boolean }) {
  const canRead = usePerm("settings:read")
  const { data, error, mutate } = useSWR<PromptRow[]>(
    canRead ? "/settings/prompts" : null,
    (key: string) => apiGet<PromptRow[]>(key),
  )
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [savedAt, setSavedAt] = useState<Record<string, string>>({})
  const [feedback, setFeedback] = useState<{ key: string; error: boolean; text: string } | null>(
    null,
  )
  const [creating, setCreating] = useState(false)
  const [newKey, setNewKey] = useState("")
  const [newContent, setNewContent] = useState("")
  const [newDescription, setNewDescription] = useState("")

  async function save(key: string, content: string, description: string) {
    setFeedback(null)
    try {
      await apiPut(`/settings/prompts/${encodeURIComponent(key)}`, { content, description })
      await mutate()
      setDrafts((previous) => {
        const next = { ...previous }
        delete next[key]
        return next
      })
      setSavedAt((previous) => ({ ...previous, [key]: new Date().toLocaleTimeString() }))
      setFeedback({ key, error: false, text: "已保存，下一道题开始生效" })
    } catch (cause) {
      setFeedback({ key, error: true, text: errorText(cause) })
    }
  }

  return (
    <section className="flex flex-col gap-1">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-0.5">
          <h2 className="font-heading text-sm font-medium">提示词</h2>
          <p className="text-xs text-muted-foreground">
            没有自定义条目时用代码内置默认；版本号每次保存 +1，是服务端唯一的变更痕迹。
          </p>
        </div>
        {canWrite ? (
          <Button size="sm" variant="outline" onClick={() => setCreating(!creating)}>
            {creating ? "取消" : "新增提示词"}
          </Button>
        ) : null}
      </div>

      {error ? <p className="text-sm text-destructive">读取失败：{errorText(error)}</p> : null}

      {creating ? (
        <div className="flex flex-col gap-2 rounded-lg border border-dashed p-3">
          <Input
            aria-label="提示词 key"
            placeholder="key，例如 quiz.system"
            value={newKey}
            onChange={(event) => setNewKey(event.target.value)}
          />
          <Textarea
            aria-label="提示词正文"
            className="min-h-24 font-mono text-xs"
            placeholder="提示词正文"
            value={newContent}
            onChange={(event) => setNewContent(event.target.value)}
          />
          <Input
            aria-label="提示词说明"
            placeholder="说明（可选，写清这版为什么这么改）"
            value={newDescription}
            onChange={(event) => setNewDescription(event.target.value)}
          />
          <Button
            size="sm"
            className="w-fit"
            disabled={newKey.trim() === "" || newContent.trim() === ""}
            onClick={() =>
              void save(newKey.trim(), newContent, newDescription).then(() => {
                setNewKey("")
                setNewContent("")
                setNewDescription("")
                setCreating(false)
              })
            }
          >
            写入
          </Button>
        </div>
      ) : null}

      {!data && !error ? <Skeleton className="h-24 w-full" /> : null}
      {data && data.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          还没有自定义提示词 —— 答题时用代码里的内置默认，需要微调再新增一条。
        </p>
      ) : null}

      {data?.map((row) => (
        <Card key={row.key} size="sm">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 font-mono text-sm">
              {row.key}
              <Badge variant="outline">v{row.version}</Badge>
            </CardTitle>
            <CardDescription>
              {row.description || "未写说明"} ·{" "}
              {savedAt[row.key] ? `本会话 ${savedAt[row.key]} 保存` : "本会话未改"}
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            <Textarea
              aria-label={`${row.key} 的正文`}
              className="min-h-32 font-mono text-xs"
              value={drafts[row.key] ?? row.content}
              disabled={!canWrite}
              onChange={(event) =>
                setDrafts((previous) => ({ ...previous, [row.key]: event.target.value }))
              }
            />
            <div className="flex items-center gap-3">
              {canWrite ? (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={drafts[row.key] === undefined}
                  onClick={() =>
                    void save(row.key, drafts[row.key] ?? row.content, row.description)
                  }
                >
                  保存
                </Button>
              ) : (
                <span className="text-xs text-muted-foreground">需要 prompt:write 权限</span>
              )}
              {feedback?.key === row.key ? (
                <span
                  role={feedback.error ? "alert" : "status"}
                  className={
                    feedback.error ? "text-xs text-destructive" : "text-xs text-muted-foreground"
                  }
                >
                  {feedback.text}
                </span>
              ) : null}
            </div>
          </CardContent>
        </Card>
      ))}
    </section>
  )
}
