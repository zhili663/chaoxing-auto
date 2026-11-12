import { apiDelete, apiGet, apiPatch, apiPost } from "@/lib/api"
import { hashPassword } from "@/lib/crypto"
import type { SessionItem, UserItem } from "@/pages/users/types"

/**
 * 建号。
 *
 * 哈希必须先做：`auth/register.py::_guard_registration` 用 `looks_like_client_secret`
 * 只认 sha256(password) 的 64 位十六进制串 —— 与登录页**同一口径**
 * （共用 `@/lib/crypto.hashPassword`，别在这里再抄一份）。
 */
export async function createUser(input: {
  username: string
  /** 初始口令；**留空 = 不传**，后端按「手机号后 6 位」派生默认口令。 */
  password?: string
  displayName: string
  /** 选填手机号：留空即不传（后端存成 NULL）。 */
  phone?: string
  role: string
}): Promise<UserItem> {
  const body: Record<string, unknown> = {
    username: input.username,
    display_name: input.displayName,
    phone: input.phone || null,
    role: input.role,
  }
  // 空口令绝不能哈希成 sha256("") 发过去 —— 那等于把账号设成空口令。
  // 留空就干脆不传 secret，由后端按「手机号后 6 位」派生。
  if (input.password) body.secret = await hashPassword(input.password)
  return apiPost<UserItem>("/users", body)
}

export function grantRole(userId: number, role: string): Promise<UserItem> {
  return apiPost<UserItem>(`/users/${userId}/roles`, { role })
}

export function revokeRole(userId: number, role: string): Promise<UserItem> {
  return apiDelete<UserItem>(`/users/${userId}/roles/${role}`)
}

/** 启用 / 停用（只封登录，数据保留，所以不是删除）。 */
export function setUserActive(userId: number, active: boolean): Promise<UserItem> {
  return apiPatch<UserItem>(`/users/${userId}`, { is_active: active })
}

/** 删除账号（不可逆，会话按外键级联清除）。 */
export function deleteUser(userId: number): Promise<unknown> {
  return apiDelete<unknown>(`/users/${userId}`)
}

/** 该用户的登录会话（已撤销的也返回，前端用 `revoked_at` 区分）。 */
export function listUserSessions(userId: number): Promise<SessionItem[]> {
  return apiGet<SessionItem[]>(`/users/${userId}/sessions`)
}
