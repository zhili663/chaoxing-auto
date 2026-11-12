import { SearchIcon } from "lucide-react"
import { useMemo, useState } from "react"

import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { usePerm } from "@/lib/auth"
import { useAccounts, useGroups } from "@/lib/hooks"

import { AccountCreateDialog } from "./account-create-dialog"
import { AccountDetailSheet } from "./account-detail-sheet"
import { AccountList } from "./account-list"
import { ownerLabel, useAccountGroups, useOwnerNames } from "./data"

/**
 * 超星账号台账（`/accounts`）。
 *
 * 账号与课程分页：这里只回答"有哪些账号、属于谁、能不能用、要不要人工介入"，
 * 选课与刷课在 `/courses`。列表负责扫描与常用操作，配置细节进详情 Sheet。
 */
export function AccountsPage() {
  const canWrite = usePerm("account:write")
  const [query, setQuery] = useState("")
  const [groupId, setGroupId] = useState<number | null>(null)
  const [detailId, setDetailId] = useState<number | null>(null)
  const [notice, setNotice] = useState("")

  const { data: groups = [], mutate: mutateGroups } = useGroups()
  const { data: accounts = [], error, isLoading, mutate } = useAccounts(groupId)
  const ownerNames = useOwnerNames()
  const accountGroups = useAccountGroups(groups)

  // 成员变动要同时刷新：账号列表、组（成员数）、账号→组反向索引
  function refresh() {
    void mutate()
    void mutateGroups()
    accountGroups.revalidate()
  }

  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase()
    if (!keyword) return accounts
    return accounts.filter(
      (item) =>
        item.note.toLowerCase().includes(keyword) || item.username.toLowerCase().includes(keyword),
    )
  }, [accounts, query])

  const detail = detailId === null ? null : (accounts.find((item) => item.id === detailId) ?? null)

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="font-heading text-lg font-medium">超星账号</h1>
        <p className="text-sm text-muted-foreground">
          账号台账：登录态、分组与所属；选课与刷课在「课程」页。
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-48 flex-1 sm:max-w-xs">
          <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="h-11 pl-8 sm:h-9"
            placeholder="搜索备注或账号"
            aria-label="搜索账号"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        <Select
          value={groupId === null ? "all" : String(groupId)}
          onValueChange={(value) => {
            const next = String(value)
            setGroupId(next === "all" ? null : Number(next))
          }}
        >
          <SelectTrigger className="h-11 w-40 sm:h-9" aria-label="按分组筛选">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部分组</SelectItem>
            {groups.map((group) => (
              <SelectItem key={group.id} value={String(group.id)}>
                {group.name}（{group.members}）
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {canWrite ? (
          <div className="ml-auto">
            <AccountCreateDialog groups={groups} onCreated={() => void mutate()} />
          </div>
        ) : null}
      </div>

      {notice ? (
        <p role="status" aria-live="polite" className="text-sm text-muted-foreground">
          {notice}
        </p>
      ) : null}

      <AccountList
        accounts={filtered}
        total={accounts.length}
        isLoading={isLoading}
        error={error}
        groups={groups}
        accountGroups={accountGroups.map}
        ownerNames={ownerNames}
        canWrite={canWrite}
        onOpenDetail={setDetailId}
        onChanged={refresh}
        onNotice={setNotice}
        onRetry={refresh}
      />

      <AccountDetailSheet
        account={detail}
        groups={groups}
        groupIds={detail ? (accountGroups.map.get(detail.id) ?? []) : []}
        owner={detail ? ownerLabel(detail.owner_user_id, ownerNames) : ""}
        open={detail !== null}
        onOpenChange={(open) => {
          if (!open) setDetailId(null)
        }}
        onChanged={refresh}
        onNotice={setNotice}
      />
    </div>
  )
}
