import { CheckIcon, ChevronsUpDownIcon } from "lucide-react"
import { useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
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
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import type { Account, Kpi } from "@/lib/hooks"
import type { RecentEvent } from "@/pages/courses/event-text"
import { RunControls } from "@/pages/courses/run-controls"

/** 账号切换器：搜索式 Combobox（账号多起来时比下拉列表好用得多）。 */
function AccountCombobox({
  accounts,
  activeId,
  onSelect,
}: {
  accounts: Account[]
  activeId: number | null
  onSelect: (id: number) => void
}) {
  const [open, setOpen] = useState(false)
  const active = accounts.find((account) => account.id === activeId) ?? null

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={<Button variant="outline" className="w-full justify-between sm:w-64" />}
      >
        <span className="truncate">{active ? active.username : "选择账号"}</span>
        <ChevronsUpDownIcon className="size-4 shrink-0 opacity-50" />
      </PopoverTrigger>
      <PopoverContent align="start" className="w-72 p-0">
        <Command>
          <CommandInput placeholder="搜索账号…" />
          <CommandList>
            <CommandEmpty>没有匹配的账号</CommandEmpty>
            <CommandGroup>
              {accounts.map((account) => (
                <CommandItem
                  key={account.id}
                  value={`${account.username} ${account.note}`}
                  onSelect={() => {
                    onSelect(account.id)
                    setOpen(false)
                  }}
                >
                  <span className="min-w-0 truncate">{account.username}</span>
                  {account.id === activeId ? (
                    <CheckIcon className="ml-auto size-4 shrink-0" />
                  ) : null}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}

/** 登录对话框：口令只在这一刻出现，不落浏览器存储。 */
function LoginDialog({ onLogin }: { onLogin: (password: string) => void }) {
  const [open, setOpen] = useState(false)
  const [password, setPassword] = useState("")
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        登录
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>登录超星账号</DialogTitle>
          <DialogDescription>口令仅本次使用，后端不存明文。</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-2">
          <Label htmlFor="cx-password" className="text-xs">
            超星口令
          </Label>
          <Input
            id="cx-password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            取消
          </Button>
          <Button
            disabled={password.length === 0}
            onClick={() => {
              onLogin(password)
              setPassword("")
              setOpen(false)
            }}
          >
            登录
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-md border px-2 py-1">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-lg leading-tight">{value}</div>
    </div>
  )
}

/** 「最近事件」：只留最近几条（全局日志面板在仪表盘，不在这里重复造滚动日志）。 */
function RecentEvents({ events }: { events: RecentEvent[] }) {
  return (
    <div className="rounded-md border">
      <div className="border-b px-3 py-1.5 text-xs font-medium text-muted-foreground">最近事件</div>
      <div className="flex min-h-16 flex-col gap-1 p-3 font-mono text-xs">
        {events.length === 0 ? <span className="text-muted-foreground">等待事件…</span> : null}
        {events.map((event) => (
          <div
            key={event.id}
            className={
              event.level === "error"
                ? "truncate text-destructive"
                : event.level === "warn"
                  ? "truncate text-amber-600"
                  : "truncate text-muted-foreground"
            }
          >
            {new Date(event.at).toLocaleTimeString("zh-CN", { hour12: false })} {event.text}
          </div>
        ))}
      </div>
    </div>
  )
}

/**
 * 顶部控制台：账号切换、登录态、主操作（扫描课程 / 开始刷课）与最近事件。
 *
 * 所有动作都是"用户显式点的" —— 没有自动重试，没有后台轮询；实时性只来自 SSE。
 */
export function ControlPanel({
  accounts,
  account,
  activeId,
  kpi,
  busy,
  message,
  notice,
  events,
  canScan,
  canRun,
  canLogin,
  onSelectAccount,
  onScanCourses,
  onRun,
  onLogin,
}: {
  accounts: Account[]
  account: Account | null
  activeId: number | null
  kpi: Kpi | null
  busy: string | null
  message: string
  notice: string
  events: RecentEvent[]
  canScan: boolean
  canRun: boolean
  canLogin: boolean
  onSelectAccount: (id: number) => void
  onScanCourses: () => void
  onRun: () => void
  onLogin: (password: string) => void
}) {
  if (accounts.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">课程</CardTitle>
          <CardDescription>还没有超星账号 —— 先到「超星账号」页添加一个。</CardDescription>
        </CardHeader>
      </Card>
    )
  }

  const scanningCourses = busy === "scan-courses"
  const running = busy === "run"

  return (
    <Card>
      <CardHeader className="gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <AccountCombobox accounts={accounts} activeId={activeId} onSelect={onSelectAccount} />
          {account ? <Badge variant="outline">{account.status}</Badge> : null}
          {account?.has_session ? (
            <Badge variant="secondary">已登录</Badge>
          ) : account ? (
            <Badge variant="destructive">未登录</Badge>
          ) : null}
          {account && !account.has_session && canLogin ? <LoginDialog onLogin={onLogin} /> : null}
        </div>
        {account ? (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Metric label="任务点" value={kpi?.points_total ?? "—"} />
            <Metric label="已完成" value={kpi?.points_done ?? "—"} />
            <Metric label="待刷" value={kpi?.points_queued ?? "—"} />
            <Metric label="待人工" value={kpi?.points_need_human ?? "—"} />
          </div>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <RunControls accountId={account?.id ?? null} />
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            disabled={!canScan || busy !== null || !account}
            onClick={onScanCourses}
          >
            {scanningCourses ? "扫描中…" : "扫描课程"}
          </Button>
          <Button disabled={!canRun || busy !== null || !account} onClick={onRun}>
            {running ? "刷课中…" : "开始刷课"}
          </Button>
        </div>
        {message ? <p className="text-xs text-muted-foreground">{message}</p> : null}
        {notice ? (
          <p className="rounded-md border border-destructive/50 bg-destructive/10 p-2 text-xs text-destructive">
            {notice}
          </p>
        ) : null}
        <RecentEvents events={events} />
      </CardContent>
    </Card>
  )
}
