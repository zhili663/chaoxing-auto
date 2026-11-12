import { AnswerStats } from "./answer-stats"
import { AttemptsPanel } from "./attempts"
import { AnswerLibrary } from "./library"

/**
 * 刷题页（`/quiz`）。
 *
 * 两块职责分开：**交卷记录**是历史事实（只读，一次交卷一行），**题库缓存**是可修正的资产
 * （人工改过就永久生效）。记录在上面 —— 进这个页面的人第一问是"我做过哪些测验"，
 * 题库是接着才关心的存量。
 */
export function QuizPage() {
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="font-heading text-lg font-medium">刷题</h1>
        <p className="text-sm text-muted-foreground">
          交卷记录与题库缓存：记录只能由刷课过程产生，题库里的答案可以人工修正。
        </p>
      </div>

      <AttemptsPanel />

      <div className="flex flex-col gap-4">
        <h2 className="font-heading text-sm font-medium text-muted-foreground">题库</h2>
        <AnswerStats />
        <AnswerLibrary />
      </div>
    </div>
  )
}
