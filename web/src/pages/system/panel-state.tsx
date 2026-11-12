import type { ComponentProps, ReactNode } from "react"

import type { Badge } from "@/components/ui/badge"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { ApiError } from "@/lib/api"

/** 状态徽章的语义档位（各面板共用同一套映射口径）。 */
export type BadgeTone = ComponentProps<typeof Badge>["variant"]

/** 错误对象 → 给人看的文案（`ApiError.message` 已是后端的中文说明）。 */
export function errorText(cause: unknown): string {
  return cause instanceof ApiError ? cause.message : String(cause)
}

/** 后端时间戳 → 本地可读（缺失时给破折号，别让单元格空着像坏了）。 */
export function stamp(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "—"
}

/** 只读面板的统一外壳：标题、说明、右上角动作。 */
export function SectionCard({
  title,
  description,
  headerRight,
  children,
}: {
  title: string
  description: string
  headerRight?: ReactNode
  children: ReactNode
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
        {headerRight ? <CardAction>{headerRight}</CardAction> : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-4">{children}</CardContent>
    </Card>
  )
}

/** 加载 / 错误 / 空 三态的统一出口；有数据时渲染 children。 */
export function PanelBody({
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
  if (error) return <p className="text-sm text-destructive">读取失败：{errorText(error)}</p>
  if (isEmpty) return <p className="text-sm text-muted-foreground">{emptyText}</p>
  return children
}
