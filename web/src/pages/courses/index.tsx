import { useCallback, useEffect, useRef, useState } from "react"

import { ApiError } from "@/lib/api"
import { usePerm } from "@/lib/auth"
import { type DomainEvent, useEventStream } from "@/lib/events"
import {
  loginAccount,
  runStudy,
  scanCourses,
  scanPoints,
  selectCourses,
  useAccounts,
  useCourses,
  useKpi,
  usePoints,
} from "@/lib/hooks"
import { ControlPanel } from "@/pages/courses/control-panel"
import { CoursePanel } from "@/pages/courses/course-panel"
import { describeEvent, type RecentEvent, shouldRefresh } from "@/pages/courses/event-text"
import { PlansPanel } from "@/pages/courses/plan-dialog"

const RECENT_LIMIT = 5

/**
 * 课程页：账号 → 课程 → 任务点 → 勾选/扫描 → 刷课计划。
 *
 * 实时性只来自 SSE（`useEventStream`）：收到落库类事件时 `mutate` 任务点与 KPI，
 * 没有轮询、没有定时器。所有写操作都是用户显式触发的。
 */
export function CoursesPage() {
  const [accountId, setAccountId] = useState<number | null>(null)
  const [courseId, setCourseId] = useState<number | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState("")
  const [notice, setNotice] = useState("")
  const [events, setEvents] = useState<RecentEvent[]>([])
  const eventId = useRef(0)

  const { data: accounts = [], mutate: mutateAccounts } = useAccounts()
  const {
    data: courses = [],
    error: coursesError,
    isLoading: coursesLoading,
    mutate: mutateCourses,
  } = useCourses(accountId)
  const { data: points = [], mutate: mutatePoints } = usePoints(courseId)
  const { data: kpi, mutate: mutateKpi } = useKpi(accountId)

  const canScan = usePerm("course:scan")
  const canSelect = usePerm("course:select")
  const canRun = usePerm("study:plan:run")
  const canLogin = usePerm("account:login")

  // 默认选中第一个账号（省一次点击，但不替用户做业务决定）
  useEffect(() => {
    if (accountId === null && accounts.length > 0) setAccountId(accounts[0].id)
  }, [accounts, accountId])

  const onEvent = useCallback(
    (event: DomainEvent) => {
      const { text, level } = describeEvent(event)
      eventId.current += 1
      setEvents((previous) =>
        [{ id: eventId.current, at: Date.now(), text, level }, ...previous].slice(0, RECENT_LIMIT),
      )
      if (shouldRefresh(event.type)) {
        void mutatePoints()
        void mutateKpi()
      }
    },
    [mutateKpi, mutatePoints],
  )

  useEventStream(onEvent)

  const account = accounts.find((item) => item.id === accountId) ?? null
  const scanningCourseKey = busy?.startsWith("scan-points:")
    ? busy.slice("scan-points:".length)
    : null
  const scanningSelected = busy === "scan-selected"

  async function withBusy(label: string, action: () => Promise<string>) {
    setBusy(label)
    setMessage("")
    setNotice("")
    try {
      setMessage(await action())
    } catch (cause) {
      setMessage(cause instanceof ApiError ? cause.message : "操作失败")
    } finally {
      setBusy(null)
    }
  }

  const handleScanCourses = () =>
    withBusy("scan-courses", async () => {
      if (!accountId) return "先选账号"
      const report = await scanCourses(accountId)
      await mutateCourses()
      return `扫描完成：共 ${report.total} 门（新增 ${report.created} / 更新 ${report.updated}）`
    })

  const handleScanPoints = (courseKey: string) =>
    withBusy(`scan-points:${courseKey}`, async () => {
      if (!accountId) return "先选账号"
      const report = await scanPoints(accountId, courseKey)
      const active = courses.find((course) => course.id === courseId)
      if (active?.course_key === courseKey) await mutatePoints()
      const warnings = report.warnings ?? []
      const warning = warnings.length > 0 ? ` · ${warnings[0]}` : ""
      return `任务点：${report.chapters} 章 / 新增 ${report.created} / 更新 ${report.updated}${warning}`
    })

  /**
   * 「扫描已勾选课程」：按课程列表顺序**一门一门** `await`（绝不 `Promise.all`）。
   *
   * 并发扫描是 2026-10-14 真机事故的根因（23 个按钮被一起点 → 学习通风控），
   * 所以这里刻意串行；中途某门失败**继续后面的**，最后汇总失败课程。
   */
  const handleScanSelected = () =>
    withBusy("scan-selected", async () => {
      if (!accountId) return "先选账号"
      const targets = courses.filter((course) => course.selected)
      if (targets.length === 0) return "先在课程表里勾选要刷的课程"
      const failures: string[] = []
      let succeeded = 0
      for (const [index, course] of targets.entries()) {
        const name = course.title || course.course_key
        setMessage(`正在扫描 ${index + 1}/${targets.length}：${name}`)
        try {
          await scanPoints(accountId, course.course_key)
          succeeded += 1
        } catch (cause) {
          const reason =
            cause instanceof ApiError && cause.code === "scan_in_progress"
              ? "上一次仍扫描中，稍后重试"
              : cause instanceof ApiError
                ? cause.message
                : "扫描失败"
          failures.push(`${name}（${reason}）`)
        }
      }
      await mutateCourses()
      await mutatePoints()
      const summary = `成功 ${succeeded} · 失败 ${failures.length}`
      return failures.length > 0 ? `${summary}：${failures.join("、")}` : summary
    })

  const handleToggle = (courseKey: string, selected: boolean) =>
    withBusy(`select:${courseKey}`, async () => {
      if (!accountId) return "先选账号"
      const keys = new Set(
        courses.filter((course) => course.selected).map((course) => course.course_key),
      )
      if (selected) keys.add(courseKey)
      else keys.delete(courseKey)
      const result = await selectCourses(accountId, [...keys])
      await mutateCourses()
      return `已勾选 ${result.selected} 门课程`
    })

  const handleLogin = (password: string) =>
    withBusy("login", async () => {
      if (!accountId) return "先选账号"
      const result = await loginAccount(accountId, password)
      await mutateAccounts()
      await mutateKpi()
      if (result.ok) return "登录成功"
      return result.need_human ? `需要人工处理：${result.message}` : `登录失败：${result.message}`
    })

  const handleRun = () =>
    withBusy("run", async () => {
      if (!accountId) return "先选账号"
      const selected = courses.filter((course) => course.selected)
      if (selected.length === 0) return "先在课程表里勾选要刷的课程"
      const report = await runStudy(
        accountId,
        selected.map((course) => course.id),
      )
      await mutateKpi()
      await mutatePoints()
      if (report.units === 0) {
        // 2026-10-14 的坑：0 个待处理任务点时运行器瞬间结束，界面却只说"运行中"。
        setNotice("没有可刷的任务点 —— 请先对已勾选课程点「扫描已勾选课程」")
        return `本次 0 个播放单元（完成 ${report.done} · 失败 ${report.failed} · 待人工 ${report.blocked}）`
      }
      return `完成 ${report.done} · 失败 ${report.failed} · 待人工 ${report.blocked}（${report.elapsed_s}s）`
    })

  return (
    <div className="flex flex-col gap-4">
      <ControlPanel
        accounts={accounts}
        account={account}
        activeId={accountId}
        kpi={kpi ?? null}
        busy={busy}
        message={message}
        notice={notice}
        events={events}
        canScan={canScan}
        canRun={canRun}
        canLogin={canLogin}
        onSelectAccount={(id) => {
          setAccountId(id)
          setCourseId(null)
        }}
        onScanCourses={handleScanCourses}
        onRun={handleRun}
        onLogin={handleLogin}
      />

      {account ? (
        <>
          <CoursePanel
            courses={courses}
            activeCourseId={courseId}
            points={points}
            onPointsChanged={() => void mutatePoints()}
            scanningCourseKey={scanningCourseKey}
            scanningSelected={scanningSelected}
            busy={busy !== null}
            canSelect={canSelect}
            canScan={canScan}
            loading={coursesLoading}
            error={coursesError ? coursesError.message : null}
            onSelectCourse={setCourseId}
            onToggle={handleToggle}
            onScanPoints={handleScanPoints}
            onScanSelected={handleScanSelected}
          />
          <PlansPanel
            accountId={accountId}
            accountName={account.username}
            selectedCount={courses.filter((course) => course.selected).length}
          />
        </>
      ) : null}
    </div>
  )
}
