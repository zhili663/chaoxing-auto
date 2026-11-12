import { EyeIcon, EyeOffIcon } from "lucide-react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import { DialogFooter } from "@/components/ui/dialog"
import {
  Field,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { ApiError } from "@/lib/api"
import { type AccountGroupItem, createAccount } from "@/lib/hooks"

import { addAccountToGroup, messageOf } from "./data"

/** 账号已入库但分组没跟上时的收尾状态：不能回滚，只能告诉用户去哪补。 */
interface CreatedPending {
  warning: string
}

/** 三段式表单（身份 / 凭据 / 备注与分组）。提交即入库，分组是第二步、失败不回滚。 */
export function AccountCreateForm({
  groups,
  onCreated,
  onClose,
}: {
  groups: AccountGroupItem[]
  onCreated: () => void
  onClose: () => void
}) {
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [note, setNote] = useState("")
  const [groupId, setGroupId] = useState("none")
  const [reveal, setReveal] = useState(false)
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<{ username?: string; form?: string }>({})
  const [pending, setPending] = useState<CreatedPending | null>(null)

  async function submit() {
    if (!username.trim()) {
      setErrors({ username: "请填写超星账号（手机号 / 学号）" })
      return
    }
    setBusy(true)
    setErrors({})
    try {
      const account = await createAccount({
        username: username.trim(),
        password,
        note: note.trim(),
      })
      onCreated()
      if (groupId === "none") {
        onClose()
        return
      }
      const groupName = groups.find((item) => String(item.id) === groupId)?.name ?? `#${groupId}`
      try {
        await addAccountToGroup(Number(groupId), account.id)
        onClose()
      } catch (cause) {
        setPending({
          warning: `账号已创建，但加入「${groupName}」失败：${messageOf(cause, "未知错误")}。可在列表的「更多操作 → 分组」里重试。`,
        })
      }
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === "account_exists") {
        setErrors({ username: cause.message })
      } else {
        setErrors({ form: messageOf(cause, "创建失败") })
      }
    } finally {
      setBusy(false)
    }
  }

  if (pending) {
    return (
      <div className="flex flex-col gap-4">
        <p className="text-sm text-amber-600">{pending.warning}</p>
        <DialogFooter>
          <Button className="h-11 sm:h-9" onClick={onClose}>
            关闭
          </Button>
        </DialogFooter>
      </div>
    )
  }

  return (
    <form
      className="flex flex-col gap-5"
      onSubmit={(event) => {
        event.preventDefault()
        void submit()
      }}
    >
      <FieldSet>
        <FieldLegend variant="label">身份</FieldLegend>
        <FieldGroup>
          <Field data-invalid={Boolean(errors.username)}>
            <FieldLabel htmlFor="acc-username">账号（手机号 / 学号）</FieldLabel>
            <Input
              id="acc-username"
              value={username}
              autoComplete="off"
              aria-invalid={Boolean(errors.username)}
              onChange={(event) => setUsername(event.target.value)}
            />
            <FieldError>{errors.username}</FieldError>
          </Field>
        </FieldGroup>
      </FieldSet>

      <FieldSet>
        <FieldLegend variant="label">凭据</FieldLegend>
        <FieldGroup>
          <Field>
            <FieldLabel htmlFor="acc-password">密码</FieldLabel>
            <InputGroup>
              <InputGroupInput
                id="acc-password"
                type={reveal ? "text" : "password"}
                value={password}
                autoComplete="new-password"
                onChange={(event) => setPassword(event.target.value)}
              />
              <InputGroupAddon align="inline-end">
                <InputGroupButton
                  size="icon-sm"
                  aria-label={reveal ? "隐藏密码" : "显示密码"}
                  onClick={() => setReveal((value) => !value)}
                >
                  {reveal ? <EyeOffIcon /> : <EyeIcon />}
                </InputGroupButton>
              </InputGroupAddon>
            </InputGroup>
            <FieldDescription>
              提交后加密入库、任何接口都不回显；留空则只能走人工登录后回填。
            </FieldDescription>
          </Field>
        </FieldGroup>
      </FieldSet>

      <FieldSet>
        <FieldLegend variant="label">备注与分组</FieldLegend>
        <FieldGroup>
          <Field>
            <FieldLabel htmlFor="acc-note">备注</FieldLabel>
            <Input
              id="acc-note"
              value={note}
              placeholder="例如：机房 A · 3 号机"
              onChange={(event) => setNote(event.target.value)}
            />
            <FieldDescription>备注比账号好认，列表优先展示它。</FieldDescription>
          </Field>
          {groups.length > 0 ? (
            <Field>
              <FieldLabel htmlFor="acc-group">分组（可选）</FieldLabel>
              <Select value={groupId} onValueChange={(value) => setGroupId(String(value))}>
                <SelectTrigger id="acc-group" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">不加入分组</SelectItem>
                  {groups.map((group) => (
                    <SelectItem key={group.id} value={String(group.id)}>
                      {group.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          ) : null}
        </FieldGroup>
      </FieldSet>

      {errors.form ? <p className="text-sm text-destructive">{errors.form}</p> : null}

      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          className="h-11 sm:h-9"
          onClick={onClose}
          disabled={busy}
        >
          取消
        </Button>
        <Button type="submit" className="h-11 sm:h-9" disabled={busy}>
          {busy ? "创建中…" : "创建账号"}
        </Button>
      </DialogFooter>
    </form>
  )
}
