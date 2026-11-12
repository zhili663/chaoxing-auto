import type { ReactNode } from "react"
import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { usePerm } from "@/lib/auth"
import { type Account, type AccountGroupItem, loginAccount } from "@/lib/hooks"

import { DeleteAccountConfirm } from "./account-delete"
import { formatTime, groupNames, maskUsername, messageOf, statusMeta } from "./data"

function InfoRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 py-1.5 text-sm">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className="min-w-0 text-right">{children}</span>
    </div>
  )
}

/**
 * 账号详情 Sheet：保留列表上下文做"检视"。
 *
 * 概览 / 会话与登录（诊断） / 分组 / 危险区四段；窄屏铺满全宽当作 Drawer 用。
 */
export function AccountDetailSheet({
  account,
  groups,
  groupIds,
  owner,
  open,
  onOpenChange,
  onChanged,
  onNotice,
}: {
  account: Account | null
  groups: AccountGroupItem[]
  groupIds: number[]
  owner: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onChanged: () => void
  onNotice: (message: string) => void
}) {
  const canLogin = usePerm("account:login")
  const [busy, setBusy] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)

  async function login() {
    if (!account) return
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

  const meta = account ? statusMeta(account.status) : null
  const names = groupNames(groups, groupIds)

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="data-[side=right]:w-full sm:max-w-md">
        <SheetHeader>
          <SheetTitle>
            {account ? account.note || maskUsername(account.username) : "账号详情"}
          </SheetTitle>
          <SheetDescription>
            账号台账详情：登录态、会话与分组；破坏性操作集中在危险区。
          </SheetDescription>
        </SheetHeader>
        {account && meta ? (
          <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4">
            <section>
              <h3 className="text-sm font-medium">概览</h3>
              <div className="divide-y">
                <InfoRow label="账号">
                  <span className="font-mono text-xs break-all">{account.username}</span>
                </InfoRow>
                <InfoRow label="属主">{owner}</InfoRow>
                <InfoRow label="状态">
                  <Badge variant={meta.variant} className={meta.tone}>
                    {meta.label}
                  </Badge>
                </InfoRow>
                <InfoRow label="启用">{account.enabled ? "已启用" : "已停用"}</InfoRow>
                <InfoRow label="优先级">{account.priority}</InfoRow>
                <InfoRow label="最近检查">{formatTime(account.last_check_at)}</InfoRow>
              </div>
            </section>

            <Separator />

            <section>
              <h3 className="text-sm font-medium">会话与登录</h3>
              <div className="divide-y">
                <InfoRow label="登录会话">
                  {account.has_session ? (
                    <Badge variant="secondary" className="text-emerald-600">
                      有效
                    </Badge>
                  ) : (
                    <Badge variant="outline">无</Badge>
                  )}
                </InfoRow>
                <InfoRow label="已存密码">{account.has_password ? "有" : "无"}</InfoRow>
              </div>
              <p className="pt-1.5 text-xs text-muted-foreground">
                {account.has_password
                  ? "可直接触发登录；密码只存在服务端，不会回显。"
                  : "没有保存密码：需在超星 App 完成登录后回填，或走人工处理。"}
              </p>
              {canLogin ? (
                <Button
                  variant="outline"
                  className="mt-2 h-11 w-full sm:h-10"
                  disabled={busy}
                  onClick={() => void login()}
                >
                  {busy ? "登录中…" : "登录"}
                </Button>
              ) : null}
            </section>

            <Separator />

            <section>
              <h3 className="text-sm font-medium">分组</h3>
              {names.length === 0 ? (
                <p className="text-sm text-muted-foreground">未加入任何分组。</p>
              ) : (
                <div className="flex flex-wrap gap-1 pt-1">
                  {names.map((name) => (
                    <Badge key={name} variant="outline">
                      {name}
                    </Badge>
                  ))}
                </div>
              )}
              <p className="pt-1.5 text-xs text-muted-foreground">
                分组的加入/移出在列表的「更多操作 → 分组」里完成。
              </p>
            </section>

            <Separator />

            <section>
              <h3 className="text-sm font-medium text-destructive">危险区</h3>
              <p className="pt-1 text-xs text-muted-foreground">
                删除会连同该账号的课程与任务点一起清理，且不可恢复；暂停使用请改用「停用」。
              </p>
              <Button
                variant="destructive"
                className="mt-2 h-11 w-full sm:h-10"
                onClick={() => setConfirmOpen(true)}
              >
                删除账号
              </Button>
              <DeleteAccountConfirm
                account={account}
                open={confirmOpen}
                onOpenChange={setConfirmOpen}
                onDeleted={() => {
                  onOpenChange(false)
                  onChanged()
                }}
                onNotice={onNotice}
              />
            </section>
          </div>
        ) : null}
      </SheetContent>
    </Sheet>
  )
}
