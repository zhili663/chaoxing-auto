import { useState } from "react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { apiPost } from "@/lib/api"

/**
 * 测验验证码接力：显示后端落盘的验证码图片，人工读码后带 `input_code` 重试该任务点。
 *
 * 触发条件是任务点状态 `need_human` —— 后端 `_dispatch` 遇到 `HumanRequired` 时落的。
 * 真机背景（2026-10-16）：服务端把某些答卷转入「要求人工验证码」态后，任何自动化提交
 * 都只会拿到「验证码不正确，请重新输入」；服务端同时把图片存到 `data/captcha/`，
 * 这一步必须由人读四位码后带回去重试。
 */
export function CaptchaDialog({
  pointId,
  pointTitle,
  onClose,
  onResolved,
}: {
  pointId: number
  pointTitle: string
  onClose: () => void
  onResolved: () => void
}) {
  const [code, setCode] = useState("")
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState("")

  async function submit() {
    const value = code.trim()
    if (value.length === 0 || busy) {
      return
    }
    setBusy(true)
    setMessage("")
    try {
      const result = await apiPost<{ status: string }>(`/study/points/${pointId}/captcha`, {
        input_code: value,
      })
      setBusy(false)
      onResolved()
      if (result.status === "done" || result.status === "already_done") {
        onClose()
        return
      }
      setCode("")
      setMessage(`已提交（${result.status}）—— 若服务端仍要求验证码，请重新打开取新图`)
    } catch (error) {
      setBusy(false)
      setMessage(error instanceof Error ? error.message : "提交失败")
    }
  }

  // 带时间戳：同一份答卷的验证码图片每次都要重新取，绝不能让浏览器用缓存
  const src = `/api/study/points/${pointId}/captcha?ts=${Date.now()}`

  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>输入验证码</DialogTitle>
          <DialogDescription>{pointTitle || `任务点 ${pointId}`}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <img src={src} alt="验证码" className="h-10 rounded border bg-white" />
          <div className="space-y-1">
            <Label htmlFor="captcha-code">验证码</Label>
            <Input
              id="captcha-code"
              value={code}
              autoFocus
              onChange={(event) => setCode(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  void submit()
                }
              }}
              placeholder="图片上的字符"
            />
          </div>
          {message ? <p className="text-xs text-muted-foreground">{message}</p> : null}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button onClick={() => void submit()} disabled={busy || code.trim().length === 0}>
            {busy ? "提交中…" : "提交并继续"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
