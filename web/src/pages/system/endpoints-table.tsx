import useSWR from "swr"

import { Badge } from "@/components/ui/badge"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { apiGet } from "@/lib/api"
import { type BadgeTone, PanelBody, SectionCard } from "./panel-state"

/** 一个已注册端点（与 `GET /system/endpoints` 一致）。 */
interface EndpointRow {
  name: string
  method: string
  path: string
  calibration: string
  note: string
}

/** 协议置信度：决定"能不能直接用"，所以放在名字后面显眼处。 */
const CALIBRATION: Record<string, { label: string; variant: BadgeTone; hint: string }> = {
  verified: { label: "已验证", variant: "default", hint: "线上实测确认，参数按页面透传即可用" },
  template: { label: "模板", variant: "outline", hint: "路径可信，参数需用真实抓包核对" },
  volatile: { label: "易变", variant: "destructive", hint: "高频改版接口，默认关闭，需显式启用" },
}

/** 端点覆盖：协议校准的读侧，与 `cx dev probe endpoints` 同一份注册表。 */
export function EndpointsTable() {
  const { data, error, isLoading } = useSWR<EndpointRow[]>("/system/endpoints", (key: string) =>
    apiGet<EndpointRow[]>(key),
  )
  const rows = data ?? []
  const counts = rows.reduce<Record<string, number>>((accumulator, row) => {
    accumulator[row.calibration] = (accumulator[row.calibration] ?? 0) + 1
    return accumulator
  }, {})

  return (
    <SectionCard
      title="端点覆盖"
      description={`注册表共 ${rows.length} 个端点：已验证 ${counts.verified ?? 0} / 模板 ${counts.template ?? 0} / 易变 ${counts.volatile ?? 0}。置信度决定"能不能直接用"。`}
    >
      <PanelBody
        isLoading={isLoading}
        error={error}
        isEmpty={rows.length === 0}
        emptyText="还没有登记任何端点。"
      >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>端点</TableHead>
              <TableHead className="w-20">方法</TableHead>
              <TableHead>路径</TableHead>
              <TableHead className="w-24">置信度</TableHead>
              <TableHead className="w-64">备注</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => {
              const meta = CALIBRATION[row.calibration]
              return (
                <TableRow key={row.name}>
                  <TableCell className="font-mono text-xs">{row.name}</TableCell>
                  <TableCell className="text-xs">{row.method}</TableCell>
                  <TableCell className="max-w-0 font-mono text-xs">
                    <span className="block truncate" title={row.path}>
                      {row.path}
                    </span>
                  </TableCell>
                  <TableCell>
                    <Badge variant={meta?.variant ?? "ghost"} title={meta?.hint}>
                      {meta?.label ?? row.calibration}
                    </Badge>
                  </TableCell>
                  <TableCell className="max-w-0">
                    <span className="block truncate text-xs text-muted-foreground" title={row.note}>
                      {row.note || "—"}
                    </span>
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </PanelBody>
    </SectionCard>
  )
}
