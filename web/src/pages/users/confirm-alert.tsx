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
import { ApiError } from "@/lib/api"

interface ConfirmAlertProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: string
  confirmLabel: string
  /** 破坏性动作（删除）用危险色；停用/启用是中性动作。 */
  destructive?: boolean
  /** 抛错时弹窗保持打开并把后端 message 显示出来，成功才关闭。 */
  onConfirm: () => Promise<void>
}

/**
 * 破坏性动作的二次确认（受控）。
 *
 * 做成受控组件是因为同一组动作有两个入口（列表行菜单 / 详情 Dialog 危险区）：
 * 文案、busy、错误提示不该写两遍，而入口不同又没法共用同一个 Trigger。
 */
export function ConfirmAlert({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  destructive = true,
  onConfirm,
}: ConfirmAlertProps) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")

  async function confirm() {
    setBusy(true)
    setError("")
    try {
      await onConfirm()
      onOpenChange(false)
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "操作失败，请稍后再试")
    } finally {
      setBusy(false)
    }
  }

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        // 请求在途时不许关窗：关掉会让用户以为已经执行完，实际还在飞
        if (busy) return
        setError("")
        onOpenChange(next)
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        {error ? <p className="text-sm text-destructive">{error}</p> : null}
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction
            variant={destructive ? "destructive" : "default"}
            disabled={busy}
            onClick={() => void confirm()}
          >
            {confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
