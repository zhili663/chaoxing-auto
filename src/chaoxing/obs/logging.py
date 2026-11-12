"""可观测性层（四通道之一：app 日志）。

四个通道的落点见 ``docs/REFACTOR_PLAN.md`` §5.6：

===============  ===========================  ==========================
通道              载体                          说明
===============  ===========================  ==========================
app 日志         本模块（structlog）            进程 / 请求 / 依赖健康度
刷课记录           storage（P2 落表）             任务点事件流水
刷题记录           storage + ai（P2/P3）          题目、答案来源、置信度
行为审计           storage（P4 中间件写入）        谁在何时对什么做了什么
===============  ===========================  ==========================
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, MutableMapping, Sequence
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import IO, Any, cast

import structlog

from chaoxing.obs.log_stream import LogStreamHandler

#: structlog 处理器的调用签名（只用于类型标注，避免 list[object] 那种宽类型）
Processor = Callable[[Any, str, MutableMapping[str, Any]], Any]

_configured = False


def _build_processors() -> list[Processor]:
    """构造日志处理链（stdlib 与 structlog 共用同一份前置链）。

    **不含广播**：SSE 的日志流走 :class:`~chaoxing.obs.log_stream.LogStreamHandler`
    （独立 handler，每个 record 只 ``emit`` 一次）。放进这条链会被每个 handler 各跑一遍，
    同一条日志就出现两次。
    """
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]


def setup_logging(
    level: str = "INFO",
    *,
    log_dir: Path | None = None,
    json_output: bool = True,
    stream: IO[str] | None = None,
) -> None:
    """配置 structlog + stdlib logging（幂等，可重复调用）。

    :param level: 日志级别名（DEBUG / INFO / WARNING / ERROR）
    :param log_dir: 若给出则同时写按天轮转的 ``app.log``（保留 7 天）
    :param json_output: True 输出 JSON 行（便于采集），False 输出可读文本
    :param stream: 控制台输出流，默认 stderr（stdout 留给 CLI 协议与管道）
    """
    global _configured

    shared = _build_processors()
    renderer: Processor = (
        cast("Processor", structlog.processors.JSONRenderer(ensure_ascii=False))
        if json_output
        else cast("Processor", structlog.dev.ConsoleRenderer(colors=False))
    )

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=cast("Sequence[Processor]", shared),
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handlers: list[logging.Handler] = [logging.StreamHandler(stream or sys.stderr)]
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            TimedRotatingFileHandler(
                log_dir / "app.log", when="midnight", backupCount=7, encoding="utf-8"
            )
        )

    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
    # 日志流（SSE）用独立 handler：每个 record 只 emit 一次 —— 放进 formatter 链
    # 会被"每个 handler 各跑一遍"，同一条日志在面板里出现两次（实测见 LogStreamHandler）
    root.addHandler(LogStreamHandler())
    root.setLevel(level.upper())

    _configured = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """取一个绑定了 ``logger`` 名的结构化日志器。

    :param name: 通常是模块名（``__name__``）
    :returns: structlog 的 :class:`~structlog.stdlib.BoundLogger`
    """
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(name))


def is_configured() -> bool:
    """本进程是否已经调用过 :func:`setup_logging`。"""
    return _configured
