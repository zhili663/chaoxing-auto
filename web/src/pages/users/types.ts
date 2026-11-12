/** 平台用户条目（与后端 `api/routers/users/routes.py::UserItem` 一致）。 */
export interface UserItem {
  id: number
  username: string
  display_name: string
  is_active: boolean
  is_superuser: boolean
  last_login_at: string | null
  roles: string[]
}

/** 一条登录会话（与后端 `GET /users/{id}/sessions` 一致，**不含 token**）。 */
export interface SessionItem {
  id: number
  ip: string
  user_agent: string
  created_at: string
  last_seen_at: string | null
  revoked_at: string | null
}

/** `GET /roles` 的形状：角色名 → 权限节点（后端从 `BUILTIN_ROLES` 派生）。 */
export type RoleMap = Record<string, string[]>

/** 时间戳：空值给「—」而不是空串，表格里一眼能看出"没有数据"而非"加载失败"。 */
export function formatDateTime(value: string | null): string {
  if (!value) return "—"
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

/**
 * 为什么不能停用该用户（`null` = 可以）。
 *
 * 后端 `PATCH /users/{id}` 会对"改自己"返回 400 `cannot_modify_self` ——
 * 前端提前给出理由并禁用按钮，用户才不会点了才知道不行。
 */
export function activeLockReason(target: UserItem, currentUserId: number): string | null {
  if (target.id === currentUserId) return "不能停用自己的账号"
  return null
}

/** 为什么不能删除该用户（`null` = 可以）。删超管必须是超管，后端也会 403。 */
export function deleteLockReason(
  target: UserItem,
  currentUserId: number,
  currentUserIsSuperuser: boolean,
): string | null {
  if (target.id === currentUserId) return "不能删除自己的账号"
  if (target.is_superuser && !currentUserIsSuperuser) return "只有超管能删除超管账号"
  return null
}
