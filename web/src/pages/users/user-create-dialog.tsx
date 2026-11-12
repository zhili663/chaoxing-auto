import { useState } from "react"

import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { ApiError } from "@/lib/api"
import { createUser, grantRole } from "@/pages/users/api"
import type { RoleMap } from "@/pages/users/types"

interface UserCreateDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  roles: RoleMap
  /** SWR 的 `mutate` 返回值不是 void，这里只要求"能 await 完"，不关心刷新结果。 */
  onCreated: () => Promise<unknown>
}

interface FormErrors {
  username?: string
  phone?: string
  password?: string
  roles?: string
}

/**
 * 添加用户（身份 + 初始口令 + 初始角色）。
 *
 * 走 Dialog 而不是内联表单：建号是有明确边界的低频动作，页面主区该留给"扫用户"，
 * 这符合 `docs/UX.md` 的「添加 → 二级 surface」。
 */
export function UserCreateDialog({ open, onOpenChange, roles, onCreated }: UserCreateDialogProps) {
  const [username, setUsername] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [phone, setPhone] = useState("")
  const [password, setPassword] = useState("")
  const [selected, setSelected] = useState<string[]>([])
  const [errors, setErrors] = useState<FormErrors>({})
  const [failure, setFailure] = useState("")
  const [busy, setBusy] = useState(false)

  const roleNames = Object.keys(roles)

  function reset() {
    setUsername("")
    setDisplayName("")
    setPhone("")
    setPassword("")
    setSelected([])
    setErrors({})
    setFailure("")
  }

  function validate(): FormErrors {
    const found: FormErrors = {}
    if (username.trim().length < 3) found.username = "用户名至少 3 个字符"
    if (password.length > 0 && password.length < 6) found.password = "口令至少 6 位"
    if (password.length === 0 && !phone.trim())
      found.password = "不填口令时必须填手机号（默认口令取后 6 位）"
    if (password.length === 0 && selected.includes("admin"))
      found.password = "管理员账号必须显式设置初始口令"
    if (selected.length === 0) found.roles = "至少选择一个初始角色"
    return found
  }

  async function submit() {
    const found = validate()
    setErrors(found)
    setFailure("")
    if (Object.values(found).some(Boolean)) return

    setBusy(true)
    try {
      // 建号接口只收一个初始角色：第一个随创建提交，其余逐个补授
      const created = await createUser({
        username: username.trim(),
        password,
        displayName: displayName.trim(),
        phone: phone.trim(),
        role: selected[0],
      })
      for (const role of selected.slice(1)) {
        await grantRole(created.id, role)
      }
      await onCreated()
      reset()
      onOpenChange(false)
    } catch (cause) {
      if (!(cause instanceof ApiError)) {
        setFailure("创建失败，请稍后再试")
      } else if (cause.code === "user_exists") {
        // 撞的是用户名还是手机号，看服务端文案（都走 user_exists 这一个码）
        setErrors(
          cause.message.includes("手机号") ? { phone: cause.message } : { username: cause.message },
        )
      } else if (cause.code === "invalid_secret") {
        setErrors({ password: cause.message })
      } else {
        setFailure(cause.message)
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (busy) return
        if (!next) reset()
        onOpenChange(next)
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>添加用户</DialogTitle>
          <DialogDescription>
            口令在浏览器里做 sha256，明文不会离开本机；角色可稍后在详情里调整。
          </DialogDescription>
        </DialogHeader>
        <FieldGroup>
          <Field data-invalid={Boolean(errors.username)}>
            <FieldLabel htmlFor="user-username">用户名</FieldLabel>
            <Input
              id="user-username"
              value={username}
              autoComplete="off"
              aria-invalid={Boolean(errors.username)}
              onChange={(event) => setUsername(event.target.value)}
            />
            <FieldError errors={[{ message: errors.username }]} />
          </Field>
          <Field data-invalid={Boolean(errors.password)}>
            <FieldLabel htmlFor="user-password">初始口令（可留空）</FieldLabel>
            <Input
              id="user-password"
              type="password"
              value={password}
              autoComplete="new-password"
              aria-invalid={Boolean(errors.password)}
              onChange={(event) => setPassword(event.target.value)}
            />
            <FieldDescription>
              留空 = 默认取「手机号后 6 位」（管理员角色必须显式设置）；填了则至少 6 位。
            </FieldDescription>
            <FieldError errors={[{ message: errors.password }]} />
          </Field>
          <Field>
            <FieldLabel htmlFor="user-display-name">展示名</FieldLabel>
            <Input
              id="user-display-name"
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
            />
            <FieldDescription>可留空，界面上将退回显示用户名。</FieldDescription>
          </Field>
          <Field data-invalid={Boolean(errors.phone)}>
            <FieldLabel htmlFor="user-phone">手机号</FieldLabel>
            <Input
              id="user-phone"
              value={phone}
              inputMode="tel"
              autoComplete="off"
              placeholder="选填，用于登录"
              aria-invalid={Boolean(errors.phone)}
              onChange={(event) => setPhone(event.target.value)}
            />
            <FieldDescription>填了之后也能用手机号登录，且需保持唯一。</FieldDescription>
            <FieldError errors={[{ message: errors.phone }]} />
          </Field>
          <Field data-invalid={Boolean(errors.roles)}>
            {/* 用普通标题而非 FieldLabel：角色是一组 Checkbox，没有单个可关联的控件 */}
            <div className="text-sm font-medium">初始角色</div>
            <div className="flex flex-col gap-2">
              {roleNames.map((name) => (
                <div key={name} className="flex items-start gap-2 text-sm">
                  <Checkbox
                    checked={selected.includes(name)}
                    aria-label={`角色 ${name}`}
                    onCheckedChange={(checked) =>
                      setSelected((prev) =>
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
            <FieldError errors={[{ message: errors.roles }]} />
          </Field>
        </FieldGroup>
        {failure ? <p className="text-sm text-destructive">{failure}</p> : null}
        <DialogFooter>
          <Button variant="outline" disabled={busy} onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button disabled={busy} onClick={() => void submit()}>
            创建
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
