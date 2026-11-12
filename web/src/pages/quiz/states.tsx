import type { ReactNode } from "react"

import { Skeleton } from "@/components/ui/skeleton"
import { ApiError } from "@/lib/api"

/**
 * 加载 / 错误 / 空 三态的统一出口；有数据时渲染 children。
 *
 * 三态分开写而不是"用假数据顶着"：加载中给骨架屏，出错说清原因，空态给下一步动作 ——
 * 让人分得清"还没好"和"确实没有"。
 */
export function States({
  isLoading,
  error,
  isEmpty,
  emptyText,
  children,
}: {
  isLoading: boolean
  error: unknown
  isEmpty: boolean
  emptyText: string
  children: ReactNode
}) {
  if (isLoading) return <Skeleton className="h-32 w-full" />
  if (error) {
    const reason = error instanceof ApiError ? error.message : String(error)
    return <p className="text-sm text-destructive">读取失败：{reason}</p>
  }
  if (isEmpty) return <p className="text-sm text-muted-foreground">{emptyText}</p>
  return children
}
