import useSWR from "swr"

import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { apiGet } from "@/lib/api"

/** 概览四块（与 `/quiz/stats` 的键一一对应）。 */
const CARDS = [
  { label: "题目总数", key: "total" },
  { label: "人工录入", key: "manual" },
  { label: "模型给出", key: "model" },
  { label: "累计命中", key: "hits" },
]

/** 题库概览：回答"题库攒了多少、人改过几条"。 */
export function AnswerStats() {
  const { data: stats } = useSWR<Record<string, number>>("/quiz/stats", (key: string) =>
    apiGet<Record<string, number>>(key),
  )

  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {CARDS.map((item) => (
        <Card key={item.key}>
          <CardHeader className="pb-2">
            <CardDescription>{item.label}</CardDescription>
            <CardTitle className="text-3xl">{stats?.[item.key] ?? "—"}</CardTitle>
          </CardHeader>
        </Card>
      ))}
    </div>
  )
}
