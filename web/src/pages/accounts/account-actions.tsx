import { CheckIcon, MoreHorizontalIcon } from "lucide-react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { usePerm } from "@/lib/auth"
import { type Account, type AccountGroupItem, loginAccount } from "@/lib/hooks"

import { DeleteAccountConfirm } from "./account-delete"
import { addAccountToGroup, messageOf, patchAccount, removeAccountFromGroup } from "./data"

/** 触控目标：移动端 44px，桌面收回到紧凑高度（桌面靠鼠标，密度优先）。 */
const TOUCH_BUTTON = "h-11 px-3 sm:h-7 sm:px-2.5"
const TOUCH_ICON = "size-11 sm:size-7"

/**
 * 行的操作区。
 *
 * 频繁操作（登录 / 详情）直接可见；变更状态与破坏性操作收进溢出菜单 ——
 * 登录结果回给页面顶部的状态条，而不是撑高表格行。
 */
export function AccountRowActions({
  account,
  groups,
  groupIds,
  onOpenDetail,
  onChanged,
  onNotice,
}: {
  account: Account
  groups: AccountGroupItem[]
  groupIds: number[]
  onOpenDetail: () => void
  onChanged: () => void
  onNotice: (message: string) => void
}) {
  const canLogin = usePerm("account:login")
  const canWrite = usePerm("account:write")
  const canDelete = usePerm("account:delete")
  const canGroup = usePerm("group:write")
  const [busy, setBusy] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)

  async function login() {
    if (!account.has_password) {
      onNotice("该账号没有保存密码：请先在超星 App 完成登录并回填密码，或走人工处理")
      return
    }
    setBusy(true)
    onNotice("")
    try {
      const result = await loginAccount(account.id)
      if (result.ok) onNotice("登录成功")
      else if (result.need_human) onNotice(`需要人工介入：${result.message || "短信 / 验证码"}`)
      else onNotice(`登录失败：${result.message || result.reason || "未知原因"}`)
      onChanged()
    } catch (cause) {
      onNotice(messageOf(cause, "登录失败"))
    } finally {
      setBusy(false)
    }
  }

  async function toggleEnabled() {
    setBusy(true)
    try {
      await patchAccount(account.id, { enabled: !account.enabled })
      onChanged()
    } catch (cause) {
      onNotice(messageOf(cause, "更新失败"))
    } finally {
      setBusy(false)
    }
  }

  async function setGroupMembership(groupId: number, member: boolean) {
    setBusy(true)
    try {
      if (member) await removeAccountFromGroup(groupId, account.id)
      else await addAccountToGroup(groupId, account.id)
      onNotice(member ? "已移出分组" : "已加入分组")
      onChanged()
    } catch (cause) {
      onNotice(messageOf(cause, member ? "移出分组失败" : "加入分组失败"))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex items-center justify-end gap-1">
      {canLogin ? (
        <Button
          variant="outline"
          size="sm"
          className={TOUCH_BUTTON}
          disabled={busy}
          onClick={() => void login()}
        >
          登录
        </Button>
      ) : null}
      <Button variant="ghost" size="sm" className={TOUCH_BUTTON} onClick={onOpenDetail}>
        详情
      </Button>
      {canWrite || canDelete || canGroup ? (
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button
                variant="ghost"
                size="icon"
                className={TOUCH_ICON}
                aria-label="更多操作"
                disabled={busy}
              />
            }
          >
            <MoreHorizontalIcon />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {canGroup ? (
              <DropdownMenuSub>
                <DropdownMenuSubTrigger>分组</DropdownMenuSubTrigger>
                <DropdownMenuSubContent>
                  {groups.length === 0 ? (
                    <DropdownMenuItem disabled>暂无分组</DropdownMenuItem>
                  ) : (
                    groups.map((group) => {
                      const member = groupIds.includes(group.id)
                      return (
                        <DropdownMenuItem
                          key={group.id}
                          disabled={busy}
                          onClick={() => void setGroupMembership(group.id, member)}
                        >
                          <CheckIcon className={member ? "" : "opacity-0"} />
                          {group.name}
                          <span className="ml-auto text-xs text-muted-foreground">
                            {member ? "移出" : "移入"}
                          </span>
                        </DropdownMenuItem>
                      )
                    })
                  )}
                </DropdownMenuSubContent>
              </DropdownMenuSub>
            ) : null}
            {canWrite ? (
              <DropdownMenuItem disabled={busy} onClick={() => void toggleEnabled()}>
                {account.enabled ? "停用" : "启用"}
              </DropdownMenuItem>
            ) : null}
            {canDelete ? (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem variant="destructive" onClick={() => setConfirmOpen(true)}>
                  删除账号
                </DropdownMenuItem>
              </>
            ) : null}
          </DropdownMenuContent>
        </DropdownMenu>
      ) : null}
      <DeleteAccountConfirm
        account={account}
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        onDeleted={onChanged}
        onNotice={onNotice}
      />
    </div>
  )
}
