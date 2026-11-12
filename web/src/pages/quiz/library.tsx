import { useState } from "react"
import useSWR from "swr"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { ApiError, apiDelete, apiGet, apiPatch } from "@/lib/api"
import { usePerm } from "@/lib/auth"

import { type AnswerItem, SOURCE_LABEL } from "./types"

/**
 * 题库：浏览、搜索与人工修正。
 *
 * 一条业务判断：**人工改过的答案来源标 `manual`，模型之后不再覆盖它** ——
 * 否则"改一次又被打回"会让人彻底放弃修正这条路。
 */
export function AnswerLibrary() {
  const canWrite = usePerm("quiz:cache:write")
  const [keyword, setKeyword] = useState("")
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [message, setMessage] = useState("")

  const { data: items = [], mutate } = useSWR<AnswerItem[]>(
    `/quiz/cache?limit=200&keyword=${encodeURIComponent(keyword)}`,
    (key: string) => apiGet<AnswerItem[]>(key),
  )

  async function save(fingerprint: string) {
    const value = drafts[fingerprint]
    if (!value) return
    setMessage("")
    try {
      await apiPatch(`/quiz/cache/${fingerprint}`, { value })
      setDrafts((previous) => {
        const next = { ...previous }
        delete next[fingerprint]
        return next
      })
      setMessage("已修正（这条之后不会再被模型覆盖）")
      await mutate()
    } catch (cause) {
      setMessage(cause instanceof ApiError ? cause.message : "修正失败")
    }
  }

  async function remove(fingerprint: string) {
    setMessage("")
    try {
      await apiDelete(`/quiz/cache/${fingerprint}`)
      setMessage("已删除（下次遇到会重新问模型）")
      await mutate()
    } catch (cause) {
      setMessage(cause instanceof ApiError ? cause.message : "删除失败")
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">题库（{items.length}）</CardTitle>
        <CardDescription>
          按命中次数排序 —— 常考的排前面；人工修正过的答案来源会变成"人工"
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <Input
          placeholder="搜索题干"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
        />
        {message ? <p className="text-sm text-muted-foreground">{message}</p> : null}

        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>题干</TableHead>
              <TableHead className="w-24">来源</TableHead>
              <TableHead className="w-16 text-right">命中</TableHead>
              <TableHead className="w-56">答案</TableHead>
              <TableHead className="w-24" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => (
              <TableRow key={item.fingerprint}>
                <TableCell className="max-w-0">
                  <div className="truncate text-sm">{item.question || "(无题干)"}</div>
                  <div className="truncate font-mono text-xs text-muted-foreground">
                    {item.fingerprint.slice(0, 12)}…
                  </div>
                </TableCell>
                <TableCell>
                  <Badge variant={item.source === "manual" ? "default" : "secondary"}>
                    {SOURCE_LABEL[item.source] ?? item.source}
                  </Badge>
                </TableCell>
                <TableCell className="text-right text-sm">{item.hits}</TableCell>
                <TableCell>
                  {canWrite ? (
                    <Input
                      value={drafts[item.fingerprint] ?? item.answer}
                      onChange={(event) =>
                        setDrafts((previous) => ({
                          ...previous,
                          [item.fingerprint]: event.target.value,
                        }))
                      }
                    />
                  ) : (
                    <span className="text-sm">{item.answer}</span>
                  )}
                </TableCell>
                <TableCell>
                  {canWrite ? (
                    <div className="flex gap-1">
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={drafts[item.fingerprint] === undefined}
                        onClick={() => void save(item.fingerprint)}
                      >
                        保存
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => void remove(item.fingerprint)}
                      >
                        删除
                      </Button>
                    </div>
                  ) : null}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {items.length === 0 ? (
          <p className="text-sm text-muted-foreground">题库还是空的 —— 刷完一轮测验就会有记录</p>
        ) : null}
      </CardContent>
    </Card>
  )
}
