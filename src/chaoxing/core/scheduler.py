"""账号并发调度：把 N 个账号的刷课任务压进有限并发。

为什么不直接 ``asyncio.gather``：

* **并发上限必须可配** —— 4C4G 预算下默认跑 3 个账号（见 ``docs/REFACTOR_PLAN.md`` §6）；
* **账号之间必须隔离** —— 一个账号炸了不能影响其它账号，报告里要能看出来是谁炸的；
* **结果要能汇总** —— 每个账号一份 :class:`~chaoxing.core.services.study.StudyReport`。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from chaoxing.core.services.study import StudyReport, StudyService

log = logging.getLogger(__name__)


@dataclass(slots=True)
class SchedulerReport:
    """一轮多账号调度的汇总。"""

    reports: dict[int, StudyReport] = field(default_factory=dict)
    errors: dict[int, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """是否没有任何账号级异常。"""
        return not self.errors

    def total_done(self) -> int:
        """所有账号合计完成的任务点数。"""
        return sum(report.done for report in self.reports.values())


class AccountScheduler:
    """账号级并发调度。

    :param study: 刷课服务
    :param max_concurrency: 同时刷课的账号数
    """

    def __init__(self, *, study: StudyService, max_concurrency: int = 3) -> None:
        self._study = study
        self._max_concurrency = max(1, max_concurrency)

    async def run(
        self,
        account_ids: Sequence[int],
        *,
        course_ids: Sequence[int] | None = None,
        limit: int | None = None,
    ) -> SchedulerReport:
        """并发刷多个账号。

        :param account_ids: 账号 ID 列表
        :param course_ids: 限定课程（可选）
        :param limit: 每账号最多执行多少任务点（试跑用）
        :returns: :class:`SchedulerReport`（账号失败也会出现在报告里，不抛异常）
        """
        semaphore = asyncio.Semaphore(self._max_concurrency)
        report = SchedulerReport()

        async def run_one(account_id: int) -> None:
            async with semaphore:
                try:
                    report.reports[account_id] = await self._study.run_account(
                        account_id, course_ids=course_ids, limit=limit
                    )
                except Exception as exc:
                    # 完整 traceback 已写进按账号隔离的日志；主日志只留一行摘要
                    log.error("账号 %s 刷课失败：%s: %s", account_id, type(exc).__name__, exc)
                    report.errors[account_id] = f"{type(exc).__name__}: {exc}"

        await asyncio.gather(*(run_one(account_id) for account_id in account_ids))
        log.info(
            "调度完成：%d 个账号成功 / %d 个失败，合计完成 %d 个任务点",
            len(report.reports),
            len(report.errors),
            report.total_done(),
        )
        return report
