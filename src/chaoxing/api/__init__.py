"""HTTP 入口层：FastAPI 应用、依赖注入、SSE、路由包（P4 落地）。

路由组织规范见 ``docs/REFACTOR_PLAN.md`` §5.11：一资源一包、单文件 ≤ 200 行、
handler ≤ 15 行、业务逻辑只允许出现在 ``core.services`` 里。
"""
