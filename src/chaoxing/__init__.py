"""chaoxing —— 学习通刷课服务（v2 架构）。

分层与依赖规则见 ``docs/REFACTOR_PLAN.md`` §2::

    api / cli          入口层（HTTP 与命令行）
    core               领域层（服务、编排、调度、事件）
    client / ai        外部适配层（学习通协议、大模型）
    storage / obs      基础设施层（持久化、可观测性）
    config / utils     全局（配置、无状态工具）

硬规则：``core`` 永不依赖 ``api`` / ``cli``；``client`` 可脱离其它层单独运行。
"""

__version__ = "2.0.0.dev0"
