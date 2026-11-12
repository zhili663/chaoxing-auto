import { useEffect, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Separator } from "@/components/ui/separator"
import { ApiError } from "@/lib/api"
import { deleteUser, grantRole, revokeRole, setUserActive } from "@/pages/users/api"
import { ConfirmAlert } from "@/pages/users/confirm-alert"
import {
  activeLockReason,
  deleteLockReason,
  formatDateTime,
  type RoleMap,
  type UserItem,
} from "@/pages/users/types"
import { UserSessions } from "@/pages/users/user-sessions"

type DangerKind = "disable" | "enable" | "delete"

interface UserDetailDialogProps {
  user: UserItem | null
  roles: RoleMap
  currentUserId: number
  currentUserIsSuperuser: boolean
  canManageRoles: boolean
  canWrite: boolean
  onOpenChange: (open: boolean) => void
  /** SWR 的 `mutate` 返回值不是 void，这里只要求"能 await 完"。 */
  onChanged: () => Promise<unknown>
}

/** 用户详情：身份 / 角色 / 会话 / 危险区，四段在同一个 Dialog 里渐进展开。 */
export function UserDetailDialog({
  user,
  roles,
  currentUserId,
  currentUserIsSuperuser,
  canManageRoles,
  canWrite,
  onOpenChange,
  onChanged,
}: UserDetailDialogProps) {
  const [selectedRoles, setSelectedRoles] = useState<string[]>([])
  const [roleError, setRoleError] = useState("")
  const [roleBusy, setRoleBusy] = useState(false)
  const [danger, setDanger] = useState<DangerKind | null>(null)

  // 换人时重置草稿：否则上一个用户勾过的角色会残留在下一个用户身上
  useEffect(() => {
    setSelectedRoles(user?.roles ?? [])
    setRoleError("")
    setDanger(null)
  }, [user])

  if (!user) return null

  const isSelf = user.id === currentUserId
  const rolesLocked = !canManageRoles || isSelf || user.is_superuser
  const dirty =
    selectedRoles.some((name) => !user.roles.includes(name)) ||
    user.roles.some((name) => !selectedRoles.includes(name))
  const activeLock = activeLockReason(user, currentUserId)
  const deleteLock = deleteLockReason(user, currentUserId, currentUserIsSuperuser)

  function rolesLockReason(): string {
    if (!canManageRoles) return "需要 role:manage 权限才能修改角色。"
    if (isSelf) return "不能修改自己的角色：避免误删自身权限后把自己锁在系统之外。"
    if (user?.is_superuser) return "超管持通配权限，角色设置对它没有效果。"
    return ""
  }

  async function saveRoles() {
    if (!user) return
    setRoleBusy(true)
    setRoleError("")
    try {
      // 只发差量：逐条 grant/revoke，避免"全删再全加"把审计刷成流水账
      const removed = user.roles.filter((name) => !selectedRoles.includes(name))
      const added = selectedRoles.filter((name) => !user.roles.includes(name))
      for (const name of removed) await revokeRole(user.id, name)
      for (const name of added) await grantRole(user.id, name)
      await onChanged()
    } catch (cause) {
      setRoleError(cause instanceof ApiError ? cause.message : "保存角色失败，请稍后再试")
    } finally {
      setRoleBusy(false)
    }
  }

  const lockReason = rolesLockReason()

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{user.display_name || user.username}</DialogTitle>
          <DialogDescription>
            用户名 {user.username} · ID {user.id}
          </DialogDescription>
        </DialogHeader>
        <div className="flex max-h-[70vh] flex-col gap-4 overflow-y-auto pr-1">
          <section className="flex flex-col gap-2">
            <h3 className="text-sm font-medium">身份与状态</h3>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Badge variant={user.is_active ? "secondary" : "outline"}>
                {user.is_active ? "启用" : "停用"}
              </Badge>
              {user.is_superuser ? <Badge>超管</Badge> : null}
              {user.roles.map((name) => (
                <Badge key={name} variant="secondary">
                  {name}
                </Badge>
              ))}
              {!user.is_superuser && user.roles.length === 0 ? (
                <span className="text-muted-foreground">无角色</span>
              ) : null}
              <span className="text-muted-foreground">
                最近登录：{user.last_login_at ? formatDateTime(user.last_login_at) : "从未登录"}
              </span>
            </div>
          </section>

          <Separator />

          <section className="flex flex-col gap-2">
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-sm font-medium">角色</h3>
              <Button
                size="sm"
                disabled={rolesLocked || !dirty || roleBusy}
                onClick={() => void saveRoles()}
              >
                保存角色
              </Button>
            </div>
            <div className="flex flex-col gap-2">
              {Object.keys(roles).map((name) => (
                <div key={name} className="flex items-start gap-2 text-sm">
                  <Checkbox
                    checked={selectedRoles.includes(name)}
                    disabled={rolesLocked}
                    aria-label={`角色 ${name}`}
                    onCheckedChange={(checked) =>
                      setSelectedRoles((prev) =>
                        checked === true ? [...prev, name] : prev.filter((item) => item !== name),
                      )
                    }
                  />
                  <span className="flex flex-col">
                    <span>{name}</span>
                    <span className="text-xs text-muted-foreground">
                      {(roles[name] ?? []).length} 个权限节点
                    </span>
                  </span>
                </div>
              ))}
            </div>
            {lockReason ? <p className="text-xs text-muted-foreground">{lockReason}</p> : null}
            {roleError ? <p className="text-sm text-destructive">{roleError}</p> : null}
          </section>

          <Separator />

          <section className="flex flex-col gap-2">
            <h3 className="text-sm font-medium">会话</h3>
            <UserSessions userId={user.id} />
          </section>

          <Separator />

          <section className="flex flex-col gap-3 rounded-lg border border-destructive/40 p-3">
            <h3 className="text-sm font-medium text-destructive">危险区</h3>
            {canWrite ? (
              <>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-sm">停用后该用户无法登录，数据保留。</span>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={Boolean(activeLock)}
                    onClick={() => setDanger(user.is_active ? "disable" : "enable")}
                  >
                    {user.is_active ? "停用" : "启用"}
                  </Button>
                </div>
                {activeLock ? <p className="text-xs text-muted-foreground">{activeLock}</p> : null}
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-sm">删除会连同会话与授权一起清除，不可恢复。</span>
                  <Button
                    variant="destructive"
                    size="sm"
                    disabled={Boolean(deleteLock)}
                    onClick={() => setDanger("delete")}
                  >
                    删除
                  </Button>
                </div>
                {deleteLock ? <p className="text-xs text-muted-foreground">{deleteLock}</p> : null}
              </>
            ) : (
              <p className="text-xs text-muted-foreground">
                停用、启用与删除账号需要 <code>user:write</code> 权限。
              </p>
            )}
          </section>
        </div>

        <ConfirmAlert
          open={danger === "disable" || danger === "enable"}
          onOpenChange={(open) => {
            if (!open) setDanger(null)
          }}
          destructive={false}
          title={user.is_active ? `停用 ${user.username}` : `启用 ${user.username}`}
          description={
            user.is_active
              ? "该用户将无法登录，数据保留，随时可以再启用。"
              : "该用户将可以重新登录。"
          }
          confirmLabel={user.is_active ? "停用账号" : "启用账号"}
          onConfirm={async () => {
            await setUserActive(user.id, !user.is_active)
            await onChanged()
          }}
        />
        <ConfirmAlert
          open={danger === "delete"}
          onOpenChange={(open) => {
            if (!open) setDanger(null)
          }}
          title={`删除 ${user.username}`}
          description="删除后该用户的会话与角色授权一并清除，且无法恢复。"
          confirmLabel="删除账号"
          onConfirm={async () => {
            await deleteUser(user.id)
            await onChanged()
            onOpenChange(false)
          }}
        />
      </DialogContent>
    </Dialog>
  )
}
