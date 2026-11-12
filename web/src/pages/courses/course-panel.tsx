import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Progress } from "@/components/ui/progress"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import type { Course, TaskPoint } from "@/lib/hooks"
import { PointsTable } from "@/pages/courses/points-table"

/** 完成度（只在当前展开的课程上有数据 —— 列表接口不下发每门的任务点计数）。 */
function completion(course: Course, activeId: number | null, points: TaskPoint[]) {
  if (course.id !== activeId) return null
  const done = points.filter((point) => point.status === "done").length
  return { done, total: points.length }
}

/** `scanned_at` 在生成类型里是可选的（后端 schema 给了默认值），所以 `undefined` 也是"未扫描"。 */
function formatTime(value: string | null | undefined): string {
  if (!value) return "未扫描"
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false })
}

function CompletionCell({
  course,
  activeId,
  points,
}: {
  course: Course
  activeId: number | null
  points: TaskPoint[]
}) {
  const stat = completion(course, activeId, points)
  if (!stat) {
    return <span className="text-xs text-muted-foreground">展开查看</span>
  }
  return (
    <div className="min-w-28">
      <Progress value={stat.total > 0 ? (stat.done / stat.total) * 100 : 0} />
      <div className="mt-1 text-xs text-muted-foreground">
        {stat.done}/{stat.total}
      </div>
    </div>
  )
}

/**
 * 课程 + 任务点：勾选是**替换式**语义（提交"当前全部已勾选"，与 CLI/API 一致）。
 *
 * 桌面端是表格（适合横向对比），窄屏塌成单列卡片（避免横向滚动）。
 */
export function CoursePanel({
  courses,
  activeCourseId,
  points,
  scanningCourseKey,
  scanningSelected,
  busy,
  canSelect,
  canScan,
  loading,
  error,
  onSelectCourse,
  onToggle,
  onScanPoints,
  onScanSelected,
  onPointsChanged,
}: {
  courses: Course[]
  activeCourseId: number | null
  points: TaskPoint[]
  onPointsChanged?: () => void
  scanningCourseKey: string | null
  scanningSelected: boolean
  busy: boolean
  canSelect: boolean
  canScan: boolean
  loading: boolean
  error: string | null
  onSelectCourse: (courseId: number) => void
  onToggle: (courseKey: string, selected: boolean) => void
  onScanPoints: (courseKey: string) => void
  onScanSelected: () => void
}) {
  const selectedCount = courses.filter((course) => course.selected).length

  return (
    <section className="flex flex-col gap-3 rounded-lg border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm font-medium">课程（{courses.length}）</div>
        <div className="flex items-center gap-2">
          <div className="text-xs text-muted-foreground">已勾选 {selectedCount} 门</div>
          <Button
            size="sm"
            variant="outline"
            disabled={!canScan || busy || selectedCount === 0}
            onClick={onScanSelected}
          >
            {scanningSelected ? "扫描中…" : `扫描已勾选课程（共 ${selectedCount} 门）`}
          </Button>
        </div>
      </div>

      {courses.length === 0 ? (
        <p
          className={`rounded-md border p-4 text-sm ${
            error ? "text-destructive" : "text-muted-foreground"
          }`}
        >
          {loading
            ? "加载课程…"
            : error
              ? `课程加载失败：${error}`
              : "还没有课程 —— 先登录账号，再点「扫描课程」。"}
        </p>
      ) : (
        <>
          <div className="hidden overflow-x-auto rounded-md border md:block">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-14">要刷</TableHead>
                  <TableHead>课程</TableHead>
                  <TableHead className="w-32">任务点完成度</TableHead>
                  <TableHead className="w-44">扫描时间</TableHead>
                  <TableHead className="w-24 text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {courses.map((course) => (
                  <TableRow
                    key={course.id}
                    className={course.id === activeCourseId ? "bg-accent/40" : undefined}
                  >
                    <TableCell>
                      <Checkbox
                        aria-label={`勾选 ${course.title || course.course_key}`}
                        checked={course.selected}
                        disabled={!canSelect}
                        onCheckedChange={(checked) => onToggle(course.course_key, checked === true)}
                      />
                    </TableCell>
                    <TableCell className="max-w-0">
                      <button
                        type="button"
                        className="block w-full text-left"
                        onClick={() => onSelectCourse(course.id)}
                      >
                        <div className="truncate text-sm">{course.title || "(无标题)"}</div>
                        <div className="truncate text-xs text-muted-foreground">
                          {course.teacher || "—"} · {course.course_key}
                        </div>
                      </button>
                    </TableCell>
                    <TableCell>
                      <CompletionCell course={course} activeId={activeCourseId} points={points} />
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {formatTime(course.scanned_at)}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={!canScan || busy}
                        onClick={() => onScanPoints(course.course_key)}
                      >
                        {scanningCourseKey === course.course_key ? "扫描中…" : "扫描任务点"}
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          {/* 窄屏：单列卡片 */}
          <ul className="flex flex-col gap-2 md:hidden">
            {courses.map((course) => (
              <li
                key={course.id}
                className={`rounded-md border p-3 ${
                  course.id === activeCourseId ? "bg-accent/40" : ""
                }`}
              >
                <div className="flex items-start gap-3">
                  <Checkbox
                    aria-label={`勾选 ${course.title || course.course_key}`}
                    checked={course.selected}
                    disabled={!canSelect}
                    className="mt-0.5"
                    onCheckedChange={(checked) => onToggle(course.course_key, checked === true)}
                  />
                  <button
                    type="button"
                    className="min-w-0 flex-1 text-left"
                    onClick={() => onSelectCourse(course.id)}
                  >
                    <div className="truncate text-sm">{course.title || "(无标题)"}</div>
                    <div className="truncate text-xs text-muted-foreground">
                      {course.teacher || "—"} · {course.course_key}
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      扫描：{formatTime(course.scanned_at)}
                    </div>
                  </button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={!canScan || busy}
                    onClick={() => onScanPoints(course.course_key)}
                  >
                    {scanningCourseKey === course.course_key ? "扫描中…" : "任务点"}
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}

      {activeCourseId !== null ? (
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2 text-sm font-medium">
            任务点（{points.length}）
            {points.some((point) => point.status === "need_human") ? (
              <Badge variant="destructive">有待人工</Badge>
            ) : null}
          </div>
          <PointsTable points={points} onRefresh={onPointsChanged} />
        </div>
      ) : null}
    </section>
  )
}
