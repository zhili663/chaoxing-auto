import type { ReactNode } from "react"

import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { usePerm } from "@/lib/auth"
import { AuditTable } from "./audit-table"
import { DoctorPanel } from "./doctor-panel"
import { EndpointsTable } from "./endpoints-table"
import { TasksTable } from "./tasks-table"

/**
 * 系统运维页：诊断 / 端点覆盖 / 任务队列 / 审计。
 *
 * 四个面板全是只读的（唯一的写操作是取消任务），所以用标签页而不是一屏铺四张长表 ——
 * 排障时人一次只关心一个问题，而且未选中的面板不挂载，也就不会白拉接口。
 */
export function SystemPage() {
  const canDoctor = usePerm("system:doctor")
  const canTask = usePerm("system:task")
  const canAudit = usePerm("system:audit")

  const tabs: { id: string; label: string; node: ReactNode }[] = [
    ...(canDoctor
      ? [
          { id: "doctor", label: "诊断", node: <DoctorPanel /> },
          { id: "endpoints", label: "端点覆盖", node: <EndpointsTable /> },
        ]
      : []),
    ...(canTask ? [{ id: "tasks", label: "任务队列", node: <TasksTable /> }] : []),
    ...(canAudit ? [{ id: "audit", label: "审计", node: <AuditTable /> }] : []),
  ]

  if (tabs.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">没有权限</CardTitle>
          <CardDescription>
            运维面板需要 `system:doctor` / `system:task` / `system:audit` 之一，请联系管理员开通。
          </CardDescription>
        </CardHeader>
      </Card>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="font-heading text-lg font-medium">系统</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          "服务在跑，但装对了吗？"——诊断回答装配，端点覆盖回答协议，任务队列回答谁在干活，审计回答谁改过什么。
        </p>
      </header>

      <Tabs defaultValue={tabs[0].id}>
        <TabsList>
          {tabs.map((tab) => (
            <TabsTrigger key={tab.id} value={tab.id}>
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>
        {tabs.map((tab) => (
          <TabsContent key={tab.id} value={tab.id}>
            {tab.node}
          </TabsContent>
        ))}
      </Tabs>
    </div>
  )
}
