import { MoreHorizontalIcon, PlusIcon, SearchIcon } from "lucide-react"
import { useMemo, useState } from "react"
import useSWR from "swr"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { apiGet } from "@/lib/api"
import { useAuth, usePerm } from "@/lib/auth"
import { deleteUser, setUserActive } from "@/pages/users/api"
import { ConfirmAlert } from "@/pages/users/confirm-alert"
import {
  activeLockReason,
  deleteLockReason,
  formatDateTime,
  type RoleMap,
  type UserItem,
} from "@/pages/users/types"
import { UserCreateDialog } from "@/pages/users/user-create-dialog"
import { UserDetailDialog } from "@/pages/users/user-detail-dialog"

type DangerKind = "disable" | "enable" | "delete"

/**
 * 平台用户页。
 *
 * 主区只回答"有哪些账号、什么角色、能不能登录、最近来过没有"（`docs/UX.md` 的扫描面）；
 * 建号、改角色、停用/删除都收进二级 surface（Dialog / AlertDialog），
 * 页面不再退化成一张扁平长表单。权限只决定入口藏不藏，真正的拦截在后端。
 */
export function UsersPage() {
  const { user: currentUser } = useAuth()
  const canWrite = usePerm("user:write")
  const canManageRoles = usePerm("role:manage")
  const { data: users = [], mutate } = useSWR<UserItem[]>("/users", (key: string) =>
    apiGet<UserItem[]>(key),
  )
  const { data: roles = {} } = useSWR<RoleMap>("/roles", (key: string) => apiGet<RoleMap>(key))

  const [query, setQuery] = useState("")
  const [createOpen, setCreateOpen] = useState(false)
  const [detailId, setDetailId] = useState<number | null>(null)
  const [pending, setPending] = useState<{ id: number; kind: DangerKind } | null>(null)

  const currentUserId = currentUser?.id ?? -1
  const isSuperuser = Boolean(currentUser?.is_superuser)

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return users
    return users.filter(
      (item) =>
        item.username.toLowerCase().includes(needle) ||
        item.display_name.toLowerCase().includes(needle),
    )
  }, [users, query])

  // 详情与确认弹窗都从列表里取"当前这份数据"：保存后 SWR 一刷新，弹窗内容跟着更新
  const detailUser = users.find((item) => item.id === detailId) ?? null
  const pendingUser = users.find((item) => item.id === pending?.id) ?? null

  async function runPending() {
    if (!pending) return
    if (pending.kind === "delete") await deleteUser(pending.id)
    else await setUserActive(pending.id, pending.kind === "enable")
    await mutate()
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>用户（{filtered.length}）</CardTitle>
          <CardDescription>角色决定权限节点；超管持通配权限</CardDescription>
          <CardAction className="flex items-center gap-2">
            <div className="relative">
              <SearchIcon className="pointer-events-none absolute top-1/2 left-2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                className="w-48 pl-7"
                placeholder="搜索用户名或展示名"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>
            {canWrite ? (
              <Button onClick={() => setCreateOpen(true)}>
                <PlusIcon />
                添加用户
              </Button>
            ) : null}
          </CardAction>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {canWrite ? null : (
            <p className="text-sm text-muted-foreground">
              新建、停用与删除用户需要 <code>user:write</code> 权限，相关入口已隐藏。
            </p>
          )}
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>用户名</TableHead>
                <TableHead>展示名</TableHead>
                <TableHead>角色</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>最近登录</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.map((item) => {
                const activeLock = activeLockReason(item, currentUserId)
                const deleteLock = deleteLockReason(item, currentUserId, isSuperuser)
                return (
                  <TableRow
                    key={item.id}
                    className="cursor-pointer"
                    tabIndex={0}
                    onClick={() => setDetailId(item.id)}
                    onKeyDown={(event) => {
                      if (event.key !== "Enter" && event.key !== " ") return
                      event.preventDefault()
                      setDetailId(item.id)
                    }}
                  >
                    <TableCell className="font-medium">{item.username}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {item.display_name || "—"}
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {item.is_superuser ? <Badge>超管</Badge> : null}
                        {item.roles.map((name) => (
                          <Badge key={name} variant="secondary">
                            {name}
                          </Badge>
                        ))}
                        {!item.is_superuser && item.roles.length === 0 ? (
                          <span className="text-xs text-muted-foreground">无</span>
                        ) : null}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant={item.is_active ? "secondary" : "outline"}>
                        {item.is_active ? "启用" : "停用"}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {item.last_login_at ? formatDateTime(item.last_login_at) : "从未登录"}
                    </TableCell>
                    <TableCell>
                      <DropdownMenu>
                        <DropdownMenuTrigger
                          render={<Button variant="ghost" size="icon-sm" aria-label="更多操作" />}
                          onClick={(event) => event.stopPropagation()}
                        >
                          <MoreHorizontalIcon />
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => setDetailId(item.id)}>
                            查看详情
                          </DropdownMenuItem>
                          {canWrite ? (
                            <>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem
                                disabled={Boolean(activeLock)}
                                onClick={() =>
                                  setPending({
                                    id: item.id,
                                    kind: item.is_active ? "disable" : "enable",
                                  })
                                }
                              >
                                {item.is_active ? "停用" : "启用"}
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                variant="destructive"
                                disabled={Boolean(deleteLock)}
                                onClick={() => setPending({ id: item.id, kind: "delete" })}
                              >
                                删除
                              </DropdownMenuItem>
                            </>
                          ) : null}
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </TableCell>
                  </TableRow>
                )
              })}
              {filtered.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="py-6 text-center text-muted-foreground">
                    {users.length === 0 ? "还没有任何用户" : "没有匹配的用户"}
                  </TableCell>
                </TableRow>
              ) : null}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <UserCreateDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        roles={roles}
        onCreated={mutate}
      />
      <UserDetailDialog
        user={detailUser}
        roles={roles}
        currentUserId={currentUserId}
        currentUserIsSuperuser={isSuperuser}
        canManageRoles={canManageRoles}
        canWrite={canWrite}
        onOpenChange={(open) => {
          if (!open) setDetailId(null)
        }}
        onChanged={mutate}
      />
      <ConfirmAlert
        open={pending !== null && pendingUser !== null}
        onOpenChange={(open) => {
          if (!open) setPending(null)
        }}
        destructive={pending?.kind === "delete"}
        title={
          pending?.kind === "delete"
            ? `删除 ${pendingUser?.username ?? ""}`
            : `${pending?.kind === "enable" ? "启用" : "停用"} ${pendingUser?.username ?? ""}`
        }
        description={
          pending?.kind === "delete"
            ? "删除后该用户的会话与角色授权一并清除，且无法恢复。"
            : pending?.kind === "enable"
              ? "该用户将可以重新登录。"
              : "该用户将无法登录，数据保留，随时可以再启用。"
        }
        confirmLabel={pending?.kind === "delete" ? "删除账号" : "确认"}
        onConfirm={runPending}
      />
    </div>
  )
}
