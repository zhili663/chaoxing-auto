import { useState } from "react"
import useSWR from "swr"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
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
import { usePerm } from "@/lib/auth"
import { useAccounts } from "@/lib/hooks"

import { AttemptDetailDialog } from "./attempt-dialog"
import { States } from "./states"
import { type AttemptRow, formatStamp, maskAccount, questionText } from "./types"

/** 单次拉取条数（后端上限 200 —— 记录页关心的是"最近这批"）。 */
const LIMIT = 200

/** 交卷与否的展示口径：`submitted` 为真才是真交上去了。 */
function SubmitBadge({ submitted }: { submitted: boolean }) {
  return (
    <Badge variant={submitted ? "secondary" : "outline"}>{submitted ? "已交卷" : "未交卷"}</Badge>
  )
}

/**
 * 交卷记录：一次交卷一行，按时间倒序。
 *
 * 只读（没有写路径 —— 记录只能由刷课过程产生）。行点击开详情 Dialog，列表上下文不丢；
 * 窄屏降级为紧凑列表：表格在手机上只能横向滚动，读起来比列表费劲得多。
 * 账号范围跟着后端口径走：普通用户只能选自己的账号，管理员可以看全部。
 */
export function AttemptsPanel() {
  const seesAll = usePerm("account:read:all")
  const { data: accounts = [], isLoading: accountsLoading } = useAccounts()
  const [accountId, setAccountId] = useState<number | null>(null)
  const [detail, setDetail] = useState<AttemptRow | null>(null)

  // 普通用户没有"全部账号"这个范围（后端会要求指定账号）：默认落到第一个自己的账号
  const scoped = accountId ?? (seesAll ? null : (accounts[0]?.id ?? null))
  const key =
    scoped === null
      ? seesAll
        ? `/quiz/attempts?limit=${LIMIT}`
        : null
      : `/quiz/attempts?limit=${LIMIT}&account_id=${scoped}`
  const { data, error, isLoading, mutate } = useSWR<AttemptRow[]>(key, (path: string) =>
    apiGet<AttemptRow[]>(path),
  )
  const rows = data ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">交卷记录</CardTitle>
        <CardDescription>
          每次交卷一行（最近的在前）。只读 —— 记录由刷课过程产生，点一行看这次交卷的明细。
        </CardDescription>
        <CardAction>
          <div className="flex items-center gap-2">
            {seesAll || accounts.length > 0 ? (
              <Select
                value={scoped === null ? "all" : String(scoped)}
                onValueChange={(value) => setAccountId(value === "all" ? null : Number(value))}
              >
                <SelectTrigger className="w-40" aria-label="按账号筛选">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {seesAll ? <SelectItem value="all">全部账号</SelectItem> : null}
                  {accounts.map((account) => (
                    <SelectItem key={account.id} value={String(account.id)}>
                      {account.note || maskAccount(account.username)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : null}
            <Button size="sm" variant="outline" disabled={isLoading} onClick={() => void mutate()}>
              刷新
            </Button>
          </div>
        </CardAction>
      </CardHeader>
      <CardContent>
        <States
          isLoading={isLoading || (accountsLoading && !seesAll)}
          error={error}
          isEmpty={rows.length === 0}
          emptyText="还没有交卷记录 —— 在课程页跑一次刷课，或先扫描任务点"
        >
          <Table className="hidden md:table">
            <TableHeader>
              <TableRow>
                <TableHead className="w-44">交卷时间</TableHead>
                <TableHead className="w-32">账号</TableHead>
                <TableHead>课程</TableHead>
                <TableHead className="w-24 text-right">题数</TableHead>
                <TableHead className="w-24">交卷</TableHead>
                <TableHead className="w-16" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow
                  key={row.id}
                  className="cursor-pointer"
                  tabIndex={0}
                  onClick={() => setDetail(row)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault()
                      setDetail(row)
                    }
                  }}
                >
                  <TableCell className="text-xs text-muted-foreground">
                    {formatStamp(row.created_at)}
                  </TableCell>
                  <TableCell className="text-sm">{row.account_label}</TableCell>
                  <TableCell className="max-w-0">
                    <span className="block truncate text-sm">
                      {row.course_title || "未关联课程"}
                    </span>
                    <span className="block truncate font-mono text-xs text-muted-foreground">
                      {row.work_id || "—"}
                    </span>
                  </TableCell>
                  <TableCell className="text-right text-sm">{questionText(row)}</TableCell>
                  <TableCell>
                    <SubmitBadge submitted={row.submitted} />
                  </TableCell>
                  <TableCell className="text-right">
                    <Button size="sm" variant="ghost" onClick={() => setDetail(row)}>
                      详情
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>

          <ul className="flex flex-col gap-2 md:hidden">
            {rows.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  className="flex w-full flex-col gap-1 rounded-lg border p-3 text-left"
                  onClick={() => setDetail(row)}
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="text-sm">{formatStamp(row.created_at)}</span>
                    <SubmitBadge submitted={row.submitted} />
                  </span>
                  <span className="truncate text-xs text-muted-foreground">
                    {row.account_label} · {row.course_title || "未关联课程"}
                  </span>
                  <span className="text-xs">
                    题数 {questionText(row)} · 作业 {row.work_id || "—"}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </States>
      </CardContent>

      <AttemptDetailDialog
        row={detail}
        onOpenChange={(open) => {
          if (!open) setDetail(null)
        }}
      />
    </Card>
  )
}
