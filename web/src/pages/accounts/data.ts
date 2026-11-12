import { useMemo } from "react"
import useSWR from "swr"

import { ApiError, apiDelete, apiGet, apiPatch, apiPost } from "@/lib/api"
import { usePerm } from "@/lib/auth"
import type { Account, AccountGroupItem } from "@/lib/hooks"

/** 状态语义：文案 + Badge 外观。颜色不作唯一载体，label 本身把状态说清。 */
export interface StatusMeta {
  label: string
  hint: string
  variant: "secondary" | "destructive" | "outline"
  tone: string
}

const STATUS_META: Record<string, StatusMeta> = {
  logged_in: { label: "已登录", hint: "会话可用", variant: "secondary", tone: "text-emerald-600" },
  pending: { label: "待登录", hint: "尚未完成首次登录", variant: "secondary", tone: "" },
  expired: {
    label: "已过期",
    hint: "会话失效，需要重新登录",
    variant: "outline",
    tone: "text-amber-600",
  },
  blocked: { label: "已封禁", hint: "被平台拦截，需要人工处理", variant: "destructive", tone: "" },
}

export function statusMeta(status: string): StatusMeta {
  return (
    STATUS_META[status] ?? { label: status || "未知", hint: "", variant: "secondary", tone: "" }
  )
}

/** 手机号脱敏：保留前 3 后 4；非 11 位（学号等）只留首尾两位。台账里不露全号。 */
export function maskUsername(username: string): string {
  if (/^1\d{10}$/.test(username)) return `${username.slice(0, 3)}****${username.slice(7)}`
  if (username.length <= 4) return username
  return `${username.slice(0, 2)}****${username.slice(-2)}`
}

/** 相对时间：列表里"多久没检查"比绝对时间戳更有用。
 *
 * `undefined` 也算"没有"：后端的 `last_check_at` 在 OpenAPI 里是可选字段（生成类型如实反映）。
 */
export function formatTime(value: string | null | undefined): string {
  if (!value) return "从未"
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return "从未"
  const diff = Date.now() - date.getTime()
  const minute = 60_000
  if (diff < minute) return "刚刚"
  if (diff < 60 * minute) return `${Math.floor(diff / minute)} 分钟前`
  if (diff < 24 * 60 * minute) return `${Math.floor(diff / (60 * minute))} 小时前`
  return date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" })
}

/** 失败原因统一取后端 message；非 API 错误给调用方兜底文案。 */
export function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback
}

/** 属主 ID → 显示名。没有 `user:read` 就不请求，表格降级为「#id / 共享」。 */
export function useOwnerNames(): Map<number, string> {
  const canRead = usePerm("user:read")
  const { data } = useSWR(canRead ? "/users" : null, () =>
    apiGet<Array<{ id: number; username: string; display_name: string }>>("/users"),
  )
  return useMemo(() => {
    const map = new Map<number, string>()
    for (const item of data ?? []) map.set(item.id, item.display_name || item.username)
    return map
  }, [data])
}

export function ownerLabel(ownerId: number | null, names: Map<number, string>): string {
  if (ownerId === null) return "共享"
  return names.get(ownerId) ?? `#${ownerId}`
}

const NO_GROUPS: Map<number, number[]> = new Map()

/**
 * 账号 → 所属组 ID。
 *
 * 后端没有"反查某个账号在哪些组"的端点，只能把每个组的成员各拉一次拼出反向索引；
 * 组是人为创建的小集合，这个代价可以接受。
 */
export function useAccountGroups(groups: AccountGroupItem[]): {
  map: Map<number, number[]>
  revalidate: () => void
} {
  const ids = groups.map((group) => group.id)
  const { data, mutate } = useSWR(
    ids.length ? `account-groups:${ids.join(",")}` : null,
    async () => {
      const lists = await Promise.all(
        ids.map(async (id) => [id, await apiGet<Account[]>(`/accounts?group_id=${id}`)] as const),
      )
      const map = new Map<number, number[]>()
      for (const [id, members] of lists) {
        for (const account of members) map.set(account.id, [...(map.get(account.id) ?? []), id])
      }
      return map
    },
  )
  return { map: data ?? NO_GROUPS, revalidate: () => void mutate() }
}

export function groupNames(groups: AccountGroupItem[], ids: number[]): string[] {
  const byId = new Map(groups.map((group) => [group.id, group.name]))
  return ids.map((id) => byId.get(id) ?? `#${id}`)
}

/** 写操作各给一个语义化函数，页面不直接拼 URL。 */
export function patchAccount(
  id: number,
  patch: { note?: string; enabled?: boolean; password?: string },
): Promise<Account> {
  return apiPatch<Account>(`/accounts/${id}`, patch)
}

export function addAccountToGroup(
  groupId: number,
  accountId: number,
): Promise<{ changed: number }> {
  return apiPost<{ changed: number }>(`/groups/${groupId}/members`, { account_ids: [accountId] })
}

/** 路径式移出：不需要 body，所以直接用 apiDelete（带 body 的批量 DELETE 接口另存）。 */
export function removeAccountFromGroup(
  groupId: number,
  accountId: number,
): Promise<{ changed: number }> {
  return apiDelete<{ changed: number }>(`/groups/${groupId}/members/${accountId}`)
}

export function removeAccount(id: number): Promise<{ ok: boolean }> {
  return apiDelete<{ ok: boolean }>(`/accounts/${id}`)
}
