# web/ —— 前端开发说明

chaoxing-auto 的前端 SPA（Vite + React 19 + TypeScript + shadcn/ui + Tailwind v4 + useSWR + ky）。
**构建产物由后端同源托管**：`web/dist` 由 FastAPI 提供（见根 `README.md` 的「三种运行形态」）。

## 常用命令

```bash
pnpm install                        # 装依赖
pnpm dev                            # 开发服务器（http://127.0.0.1:5173）
pnpm build                          # 产物 → dist/（由 API 托管）
pnpm exec biome check src           # lint + format —— CI 里跑的就是这条
```

开发时 `/api` 由 Vite 代理到 `127.0.0.1:8000`（见 `vite.config.ts`），所以后端要另开一个终端跑
`uv run cx serve`。代理让浏览器里只剩**一个源**：会话 Cookie 不需要 `SameSite=None`，
SSE 也不涉及跨域预检。

## 目录

```
src/
├── pages/            # 登录 / 仪表盘 / 课程工作台（workbench/）/ 刷题 / 用户 / 设置
├── components/ui/    # shadcn 组件（源码入库，按需改）
└── lib/              # api（ky 实例）/ auth / events（SSE）/ hooks
```

## 约定（都有守卫或机器判据）

- **实时数据一律走 SSE**：`src/lib/events.ts` 用 `useSWRSubscription` + `EventSource`，
  **没有任何定时器** —— `biome.json` 里 `setTimeout` / `setInterval` 是硬禁；
- **API 调用只用相对路径**（`prefix: "/api/"`、`EventSource("/api/events")`）：绝对地址会让 dev
  代理失效、生产变跨域。守卫在 `tests/test_no_hardcode.py::test_frontend_api_calls_are_same_origin_relative`；
- **三档断点**（375 / 768 / 1280）由 `bash scripts/web_check.sh` 实机校验（orca-cli 驱动）；
- **类型**目前手写、与后端 DTO 手工对齐（`openapi-typescript` 生成尚未接入，见重构计划 §6.5）；
- `pnpm lint`（oxlint）只是编辑器侧的补充；**CI 的前端门禁只跑 Biome**。
