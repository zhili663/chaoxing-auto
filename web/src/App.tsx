import { MenuIcon } from "lucide-react"
import { type ReactNode, useCallback, useState } from "react"
import {
  BrowserRouter,
  Navigate,
  NavLink,
  Outlet,
  Route,
  Routes,
  useLocation,
} from "react-router-dom"

import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { AuthProvider, useAuth, usePerm } from "@/lib/auth"
import { type DomainEvent, useEventStream } from "@/lib/events"
import { AccountsPage } from "@/pages/accounts"
import { CoursesPage } from "@/pages/courses"
import { DashboardPage } from "@/pages/dashboard"
import { LoginPage } from "@/pages/login"
import { QuizPage } from "@/pages/quiz"
import { SettingsPage } from "@/pages/settings"
import { SystemPage } from "@/pages/system"
import { UsersPage } from "@/pages/users"

/**
 * 主导航（信息架构见 `docs/UX.md`）。
 *
 * 账号与课程**分开**：账号是台账（谁在用、登录态如何），课程是作业面（刷什么、进度）；
 * 运维面（诊断/端点/任务/审计）从设置里独立出来 —— 设置只放全局默认值。
 */
const NAV = [
  { to: "/", label: "仪表盘", perm: "study:record:read" },
  { to: "/accounts", label: "超星账号", perm: "account:read" },
  { to: "/courses", label: "课程", perm: "course:read" },
  { to: "/quiz", label: "刷题", perm: "quiz:read" },
  { to: "/users", label: "用户", perm: "user:read" },
  { to: "/system", label: "系统", perm: "system:doctor" },
  { to: "/settings", label: "设置", perm: "settings:read" },
] as const

function NavItem({ item, onNavigate }: { item: (typeof NAV)[number]; onNavigate?: () => void }) {
  const allowed = usePerm(item.perm)
  if (!allowed) return null
  return (
    <NavLink
      to={item.to}
      end={item.to === "/"}
      onClick={onNavigate}
      className={({ isActive }) =>
        `rounded-md px-3 py-2 text-sm ${
          isActive ? "bg-accent font-medium" : "text-muted-foreground hover:bg-accent/50"
        }`
      }
    >
      {item.label}
    </NavLink>
  )
}

/** 导航主体：桌面侧栏与移动抽屉**共用同一份**，避免两套入口漂移。 */
function NavList({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className="flex flex-col gap-1">
      {NAV.map((item) => (
        <NavItem key={item.to} item={item} onNavigate={onNavigate} />
      ))}
    </nav>
  )
}

/** 窄屏导航：抽屉（7 个入口塞不进底部 tab，横滚又会让入口消失）。 */
function MobileNav() {
  const [open, setOpen] = useState(false)
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger
        render={
          <Button variant="ghost" size="icon-sm" className="lg:hidden" aria-label="打开导航" />
        }
      >
        <MenuIcon />
      </SheetTrigger>
      <SheetContent side="left" className="w-60">
        <SheetHeader>
          <SheetTitle>导航</SheetTitle>
        </SheetHeader>
        <div className="px-4 pb-4">
          <NavList onNavigate={() => setOpen(false)} />
        </div>
      </SheetContent>
    </Sheet>
  )
}

/** 全站布局：宽屏侧栏、窄屏抽屉（`main` 自己管滚动，页面内不再套一层滚动容器）。 */
function AppLayout({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth()
  return (
    <div className="flex min-h-dvh flex-col lg:flex-row">
      <aside className="hidden shrink-0 border-r p-2 lg:block lg:w-48">
        <div className="px-3 py-2 text-sm font-medium">超星自动刷课</div>
        <NavList />
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2 border-b px-4 py-2">
          <MobileNav />
          <div className="text-sm font-medium lg:hidden">超星自动刷课</div>
          <div className="ml-auto flex items-center gap-2">
            <span className="hidden text-sm text-muted-foreground sm:inline">
              {user?.display_name}
            </span>
            <Button variant="outline" size="sm" onClick={() => void logout()}>
              登出
            </Button>
          </div>
        </header>
        <main className="min-w-0 flex-1 p-4">{children}</main>
      </div>
    </div>
  )
}

/** 未登录一律去登录页（`replace` 避免回退时来回弹）。 */
function RequireAuth({ children }: { children: ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth()
  const location = useLocation()
  if (isLoading) return <div className="p-4 text-sm text-muted-foreground">加载中…</div>
  if (!isAuthenticated) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  return <>{children}</>
}

/** 已登录时不再展示登录页。 */
function RedirectIfAuthed({ children }: { children: ReactNode }) {
  const { isAuthenticated } = useAuth()
  if (isAuthenticated) return <Navigate to="/" replace />
  return <>{children}</>
}

function Shell() {
  const [lastEvent, setLastEvent] = useState<DomainEvent>()
  const onEvent = useCallback((event: DomainEvent) => setLastEvent(event), [])
  const { isAuthenticated } = useAuth()
  // 登录之后才订阅事件流（未登录订阅只会拿到 401）
  useEventStream(isAuthenticated ? onEvent : undefined)

  return (
    <Routes>
      <Route
        path="/login"
        element={
          <RedirectIfAuthed>
            <LoginPage />
          </RedirectIfAuthed>
        }
      />
      <Route
        path="/"
        element={
          <RequireAuth>
            <AppLayout>
              <Outlet />
            </AppLayout>
          </RequireAuth>
        }
      >
        <Route index element={<DashboardPage lastEvent={lastEvent} />} />
        <Route path="accounts" element={<AccountsPage />} />
        <Route path="courses" element={<CoursesPage />} />
        <Route path="quiz" element={<QuizPage />} />
        <Route path="users" element={<UsersPage />} />
        <Route path="system" element={<SystemPage />} />
        <Route path="settings" element={<SettingsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Shell />
      </BrowserRouter>
    </AuthProvider>
  )
}
