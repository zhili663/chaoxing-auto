import useSWR from "swr"

import { Badge } from "@/components/ui/badge"
import { listUserSessions } from "@/pages/users/api"
import { formatDateTime, type SessionItem } from "@/pages/users/types"

/**
 * 某个用户的登录会话（诊断信息）。
 *
 * 只在详情 Dialog 里出现：它回答的是"这个人现在在哪登录、有没有陌生来源"，
 * 属于诊断上下文；摆进列表页只会淹没扫描用的字段（UX.md 的信息层级）。
 * 组件只在 Dialog 打开时挂载，所以这里直接按 userId 拉取，不需要额外开关。
 */
export function UserSessions({ userId }: { userId: number }) {
  const { data: sessions = [], isLoading } = useSWR<SessionItem[]>(
    `/users/${userId}/sessions`,
    () => listUserSessions(userId),
  )

  if (isLoading) {
    return <p className="text-sm text-muted-foreground">正在加载会话…</p>
  }
  if (sessions.length === 0) {
    return <p className="text-sm text-muted-foreground">没有登录会话记录</p>
  }

  return (
    <div className="flex flex-col gap-1">
      {sessions.map((session) => (
        <div
          key={session.id}
          className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border px-3 py-2 text-xs"
        >
          <span className="text-muted-foreground">{session.ip || "未知 IP"}</span>
          <span
            className="min-w-0 flex-1 truncate text-muted-foreground"
            title={session.user_agent}
          >
            {session.user_agent || "未知客户端"}
          </span>
          <span className="text-muted-foreground">创建 {formatDateTime(session.created_at)}</span>
          <span className="text-muted-foreground">最近 {formatDateTime(session.last_seen_at)}</span>
          {session.revoked_at ? (
            <Badge variant="outline">已失效</Badge>
          ) : (
            <Badge variant="secondary">生效中</Badge>
          )}
        </div>
      ))}
    </div>
  )
}
