import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { ApiError } from "@/lib/api"
import type { AccountGroupItem } from "@/lib/hooks"

import { AccountCreateDialog } from "./account-create-dialog"
import { messageOf } from "./data"

const SKELETON_ROWS = [0, 1, 2, 3, 4]

/** 首屏骨架：与表格行高对齐，避免加载完成时布局跳动。 */
export function ListSkeleton() {
  return (
    <div className="flex flex-col gap-2">
      {SKELETON_ROWS.map((row) => (
        <Skeleton key={row} className="h-12 w-full" />
      ))}
    </div>
  )
}

/** 错误态分清 403 与网络/服务失败：前者无权限、重试无用，后者应重试。 */
export function ErrorState({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const forbidden = error instanceof ApiError && error.isForbidden
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed py-12 text-center">
      <p className="text-sm font-medium">{forbidden ? "没有查看账号的权限" : "账号列表加载失败"}</p>
      <p className="max-w-sm text-sm text-muted-foreground">
        {forbidden
          ? "当前用户缺少 account:read 权限，请联系管理员授权。"
          : messageOf(error, "网络异常，请确认后端在线后重试。")}
      </p>
      {forbidden ? null : (
        <Button variant="outline" onClick={onRetry}>
          重试
        </Button>
      )}
    </div>
  )
}

/** 空态给出下一步动作：直接在这里添加第一个账号。 */
export function EmptyState({
  canWrite,
  groups,
  onChanged,
}: {
  canWrite: boolean
  groups: AccountGroupItem[]
  onChanged: () => void
}) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed py-12 text-center">
      <p className="text-sm font-medium">还没有超星账号</p>
      <p className="max-w-sm text-sm text-muted-foreground">
        先添加一个账号（手机号 / 学号）并登录，之后才能扫描课程与刷课。
      </p>
      {canWrite ? (
        <AccountCreateDialog groups={groups} onCreated={onChanged} />
      ) : (
        <p className="text-sm text-muted-foreground">请联系管理员添加账号。</p>
      )}
    </div>
  )
}
