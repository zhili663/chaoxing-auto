import { useState } from "react"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import type { Account } from "@/lib/hooks"

import { maskUsername, messageOf, removeAccount } from "./data"

/** 删除确认：列表行与详情 Sheet 共用，避免两处危险操作口径漂移。 */
export function DeleteAccountConfirm({
  account,
  open,
  onOpenChange,
  onDeleted,
  onNotice,
}: {
  account: Account
  open: boolean
  onOpenChange: (open: boolean) => void
  onDeleted: () => void
  onNotice: (message: string) => void
}) {
  const [busy, setBusy] = useState(false)

  async function confirm() {
    setBusy(true)
    try {
      await removeAccount(account.id)
      onOpenChange(false)
      onNotice("账号已删除")
      onDeleted()
    } catch (cause) {
      onNotice(messageOf(cause, "删除失败"))
    } finally {
      setBusy(false)
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>删除账号 {maskUsername(account.username)}？</AlertDialogTitle>
          <AlertDialogDescription>
            该账号下的课程与任务点会一并删除，且无法恢复。只是暂时不用请改用「停用」。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel className="h-11 sm:h-9" disabled={busy}>
            取消
          </AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            className="h-11 sm:h-9"
            disabled={busy}
            onClick={() => void confirm()}
          >
            删除
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
