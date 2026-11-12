import { useState } from "react"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ApiError, apiPost } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { hashPassword } from "@/lib/crypto"

/**
 * 登录页。
 *
 * 首个用户的引导**不在界面上**（本页只有一个登录表单）：部署时用
 * ``POST /api/auth/register`` 建首位管理员（首个用户自动是超管），之后由管理员在
 * 「用户」页建号 —— 建号时初始口令可留空，默认取手机号后 6 位。
 */
export function LoginPage() {
  const { refresh } = useAuth()
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError("")
    try {
      const secret = await hashPassword(password)
      // 登录不掺 nonce：服务端手上只有 Argon2id(secret)，无法校验掺了 nonce 的 proof
      await apiPost("/auth/login", { username, secret })
      await refresh()
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "登录失败，请稍后再试")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-dvh items-center justify-center bg-muted/30 p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>超星自动刷课</CardTitle>
        </CardHeader>
        <CardContent>
          <form className="flex flex-col gap-4" onSubmit={submit}>
            <div className="flex flex-col gap-2">
              <Label htmlFor="username">手机号 / 用户名</Label>
              <Input
                id="username"
                value={username}
                autoComplete="username"
                // 只给键盘一点提示（数字优先），**不加正则强校验** —— 用户名仍要能用
                inputMode="tel"
                placeholder="手机号或用户名"
                onChange={(event) => setUsername(event.target.value)}
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="password">密码（默认手机号后六位）</Label>
              <Input
                id="password"
                type="password"
                value={password}
                autoComplete="current-password"
                onChange={(event) => setPassword(event.target.value)}
              />
            </div>
            {error ? <p className="text-sm text-destructive">{error}</p> : null}
            <Button type="submit" disabled={busy || !username || !password}>
              登录
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
