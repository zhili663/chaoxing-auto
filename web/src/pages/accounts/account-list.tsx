import { Badge } from "@/components/ui/badge"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import type { Account, AccountGroupItem } from "@/lib/hooks"

import { AccountRowActions } from "./account-actions"
import { EmptyState, ErrorState, ListSkeleton } from "./account-states"
import { formatTime, groupNames, maskUsername, ownerLabel, statusMeta } from "./data"

function GroupBadges({ groups, ids }: { groups: AccountGroupItem[]; ids: number[] }) {
  if (ids.length === 0) return <span className="text-sm text-muted-foreground">—</span>
  return (
    <div className="flex flex-wrap gap-1">
      {groupNames(groups, ids).map((name) => (
        <Badge key={name} variant="outline">
          {name}
        </Badge>
      ))}
    </div>
  )
}

function SessionBadge({ active }: { active: boolean }) {
  return active ? (
    <Badge variant="secondary" className="text-emerald-600">
      会话有效
    </Badge>
  ) : (
    <Badge variant="outline">无会话</Badge>
  )
}

export function AccountList({
  accounts,
  total,
  isLoading,
  error,
  groups,
  accountGroups,
  ownerNames,
  canWrite,
  onOpenDetail,
  onChanged,
  onNotice,
  onRetry,
}: {
  accounts: Account[]
  total: number
  isLoading: boolean
  error: unknown
  groups: AccountGroupItem[]
  accountGroups: Map<number, number[]>
  ownerNames: Map<number, string>
  canWrite: boolean
  onOpenDetail: (accountId: number) => void
  onChanged: () => void
  onNotice: (message: string) => void
  onRetry: () => void
}) {
  if (isLoading) return <ListSkeleton />
  if (error) return <ErrorState error={error} onRetry={onRetry} />
  if (total === 0) return <EmptyState canWrite={canWrite} groups={groups} onChanged={onChanged} />
  if (accounts.length === 0)
    return <p className="py-12 text-center text-sm text-muted-foreground">没有匹配的账号</p>

  return (
    <>
      <div className="hidden overflow-x-auto rounded-lg border md:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>账号</TableHead>
              <TableHead>属主</TableHead>
              <TableHead>分组</TableHead>
              <TableHead>登录态</TableHead>
              <TableHead>状态</TableHead>
              <TableHead>最近检查</TableHead>
              <TableHead className="text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {accounts.map((account) => {
              const meta = statusMeta(account.status)
              const ids = accountGroups.get(account.id) ?? []
              return (
                <TableRow key={account.id}>
                  <TableCell>
                    <div className="text-sm font-medium">
                      {account.note || maskUsername(account.username)}
                    </div>
                    {account.note ? (
                      <div className="text-xs text-muted-foreground">
                        {maskUsername(account.username)}
                      </div>
                    ) : null}
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {ownerLabel(account.owner_user_id, ownerNames)}
                  </TableCell>
                  <TableCell>
                    <GroupBadges groups={groups} ids={ids} />
                  </TableCell>
                  <TableCell>
                    <SessionBadge active={account.has_session} />
                  </TableCell>
                  <TableCell>
                    <Badge variant={meta.variant} className={meta.tone}>
                      {meta.label}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {formatTime(account.last_check_at)}
                  </TableCell>
                  <TableCell>
                    <AccountRowActions
                      account={account}
                      groups={groups}
                      groupIds={ids}
                      onOpenDetail={() => onOpenDetail(account.id)}
                      onChanged={onChanged}
                      onNotice={onNotice}
                    />
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </div>

      <div className="flex flex-col gap-2 md:hidden">
        {accounts.map((account) => {
          const meta = statusMeta(account.status)
          const ids = accountGroups.get(account.id) ?? []
          const names = groupNames(groups, ids)
          return (
            <div key={account.id} className="flex flex-col gap-2 rounded-lg border p-3">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">
                    {account.note || maskUsername(account.username)}
                  </div>
                  <div className="truncate text-xs text-muted-foreground">
                    {maskUsername(account.username)} ·{" "}
                    {ownerLabel(account.owner_user_id, ownerNames)}
                  </div>
                </div>
                <Badge variant={meta.variant} className={meta.tone}>
                  {meta.label}
                </Badge>
              </div>
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                <span>{account.has_session ? "会话有效" : "无会话"}</span>
                <span>最近检查 {formatTime(account.last_check_at)}</span>
                {names.length > 0 ? <span>分组 {names.join("、")}</span> : null}
              </div>
              <AccountRowActions
                account={account}
                groups={groups}
                groupIds={ids}
                onOpenDetail={() => onOpenDetail(account.id)}
                onChanged={onChanged}
                onNotice={onNotice}
              />
            </div>
          )
        })}
      </div>
    </>
  )
}
