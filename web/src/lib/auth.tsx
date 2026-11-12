import { createContext, type ReactNode, useContext, useMemo } from "react"
import useSWR from "swr"

import { apiGet, apiPost } from "@/lib/api"

/** 当前登录用户（后端 `/auth/me` 的形状）。 */
export interface CurrentUser {
  id: number
  username: string
  display_name: string
  is_active: boolean
  is_superuser: boolean
  permissions: string[]
}

/** 超管持通配权限，所有节点对它成立。 */
const WILDCARD = "*"

interface AuthState {
  user: CurrentUser | null
  isLoading: boolean
  /** 是否已登录（加载中时为 false，避免闪现不该看的界面）。 */
  isAuthenticated: boolean
  refresh: () => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

/**
 * 鉴权上下文：一次性把"我是谁、我有哪些权限"取回来，全站共享。
 *
 * 401 不重试也不重定向到这里 —— 由路由守卫决定去哪，组件只管渲染。
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const { data, error, isLoading, mutate } = useSWR<CurrentUser>(
    "/auth/me",
    (key) => apiGet<CurrentUser>(key),
    {
      shouldRetryOnError: false,
      revalidateOnFocus: false,
    },
  )

  const value = useMemo<AuthState>(
    () => ({
      user: error ? null : (data ?? null),
      isLoading,
      isAuthenticated: Boolean(data) && !error,
      refresh: async () => {
        await mutate()
      },
      logout: async () => {
        await apiPost("/auth/logout")
        await mutate(undefined, { revalidate: false })
      },
    }),
    [data, error, isLoading, mutate],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

/** 取鉴权上下文（必须在 {@link AuthProvider} 内使用）。 */
export function useAuth(): AuthState {
  const context = useContext(AuthContext)
  if (context === null) {
    throw new Error("useAuth 必须在 AuthProvider 内使用")
  }
  return context
}

/**
 * 权限判断：`usePerm("account:read:all")`。
 *
 * 前端据此渲染菜单与按钮，但**它只是体验优化** —— 真正的拦截在后端，
 * 隐藏按钮不等于没有接口。
 */
export function usePerm(...codes: string[]): boolean {
  const { user } = useAuth()
  if (!user) return false
  if (user.permissions.includes(WILDCARD)) return true
  return codes.every((code) => user.permissions.includes(code))
}
