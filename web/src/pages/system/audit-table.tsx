import { useState } from "react"
import useSWR from "swr"

import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { apiGet } from "@/lib/api"
import { type BadgeTone, PanelBody, SectionCard, stamp } from "./panel-state"

/** 一条审计记录（与 `GET /audit` 一致）。 */
interface AuditRow {
  id: number
  user_id: number | null
  action: string
  resource: string
  result: string
  ip: string
  request_id: string
  detail: Record<string, unknown>
  created_at: string
}

const AUDIT_RESULT: Record<string, { label: string; variant: BadgeTone }> = {
  ok: { label: "成功", variant: "secondary" },
  denied: { label: "拒绝", variant: "outline" },
  error: { label: "失败", variant: "destructive" },
}

const RESULT_OPTIONS = [
  { value: "all", label: "全部结果" },
  { value: "ok", label: "成功" },
  { value: "denied", label: "拒绝（401/403）" },
  { value: "error", label: "失败" },
]

/** 审计：每个写操作都会留痕，这里按动作 / 结果 / 操作者查最近 100 条。 */
export function AuditTable() {
  const [action, setAction] = useState("all")
  const [result, setResult] = useState("all")
  const [commitUserId, setCommitUserId] = useState("")
  const [draftUserId, setDraftUserId] = useState("")

  const actions = useSWR<string[]>("/audit/actions", (key: string) => apiGet<string[]>(key))
  const params = new URLSearchParams()
  if (action !== "all") params.set("action", action)
  if (result !== "all") params.set("result", result)
  if (commitUserId !== "") params.set("user_id", commitUserId)
  const query = params.toString()

  const { data, error, isLoading } = useSWR<AuditRow[]>(`/audit?${query}`, (key: string) =>
    apiGet<AuditRow[]>(key),
  )
  const rows = data ?? []

  return (
    <SectionCard
      title="审计"
      description="写操作（改设置、改账号、改提示词…）自动留痕；这里按时间倒序看最近 100 条，可按动作、结果、操作者收窄。"
    >
      <div className="flex flex-wrap items-end gap-3">
        <Select
          value={action}
          onValueChange={(next) => {
            if (typeof next === "string") setAction(next)
          }}
        >
          <SelectTrigger aria-label="按动作过滤" className="w-56">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部动作</SelectItem>
            {actions.data?.map((code) => (
              <SelectItem key={code} value={code}>
                <span className="font-mono">{code}</span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select
          value={result}
          onValueChange={(next) => {
            if (typeof next === "string") setResult(next)
          }}
        >
          <SelectTrigger aria-label="按结果过滤" className="w-44">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {RESULT_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Input
          type="number"
          inputMode="numeric"
          className="w-32"
          aria-label="按操作者 ID 过滤"
          placeholder="操作者 ID"
          value={draftUserId}
          onChange={(event) => setDraftUserId(event.target.value)}
          onBlur={() => setCommitUserId(draftUserId.trim())}
          onKeyDown={(event) => {
            if (event.key === "Enter") setCommitUserId(draftUserId.trim())
          }}
        />
        <span className="pb-2 text-xs text-muted-foreground">
          {actions.error ? "动作清单读取失败" : `共 ${rows.length} 条`}
        </span>
      </div>

      <PanelBody
        isLoading={isLoading}
        error={error}
        isEmpty={rows.length === 0}
        emptyText="还没有记录 —— 换个过滤条件，或先做一次写操作。"
      >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-40">时间</TableHead>
              <TableHead className="w-20">操作者</TableHead>
              <TableHead className="w-48">动作</TableHead>
              <TableHead className="w-32">结果</TableHead>
              <TableHead>资源</TableHead>
              <TableHead className="w-32">来源 IP</TableHead>
              <TableHead className="w-56">变更内容</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => {
              const meta = AUDIT_RESULT[row.result]
              const detail = Object.keys(row.detail).length > 0 ? JSON.stringify(row.detail) : ""
              return (
                <TableRow key={row.id}>
                  <TableCell className="text-xs text-muted-foreground">
                    {stamp(row.created_at)}
                  </TableCell>
                  <TableCell className="text-xs">{row.user_id ?? "—"}</TableCell>
                  <TableCell className="font-mono text-xs">{row.action}</TableCell>
                  <TableCell>
                    <Badge variant={meta?.variant ?? "ghost"}>{meta?.label ?? row.result}</Badge>
                  </TableCell>
                  <TableCell className="max-w-0 font-mono text-xs">
                    <span className="block truncate" title={row.resource}>
                      {row.resource || "—"}
                    </span>
                  </TableCell>
                  <TableCell className="font-mono text-xs">{row.ip || "—"}</TableCell>
                  <TableCell className="max-w-0">
                    <span
                      className="block truncate text-xs text-muted-foreground"
                      title={detail || undefined}
                    >
                      {detail || "—"}
                    </span>
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </PanelBody>
    </SectionCard>
  )
}
