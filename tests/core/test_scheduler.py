"""账号并发调度：并发上限、失败隔离、结果汇总。

来源：4C4G 预算下必须限制并发（默认 3 个账号），以及"一个账号炸了不能拖垮其它账号"
—— 这也是 legacy 里最容易被忽略的一条（当时是单账号串行，没有这个问题）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from chaoxing.core.scheduler import AccountScheduler
from chaoxing.core.services.study import StudyReport


class FakeStudy:
    """刷课服务替身：记录并发峰值，并按需制造失败。"""

    def __init__(self, *, fail_for: set[int] | None = None, delay: float = 0.01) -> None:
        self.fail_for = fail_for or set()
        self.delay = delay
        self.active = 0
        self.peak = 0
        self.calls: list[int] = []

    async def run_account(
        self, account_id: int, *, course_ids: Sequence[int] | None = None, limit: int | None = None
    ) -> StudyReport:
        self.calls.append(account_id)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(self.delay)
            if account_id in self.fail_for:
                raise RuntimeError(f"账号 {account_id} 炸了")
            return StudyReport(account_id=account_id, done=1)
        finally:
            self.active -= 1


async def test_concurrency_is_capped() -> None:
    """并发数被信号量压在上限内（这是 4C4G 预算的硬约束）。"""
    fake = FakeStudy()
    scheduler = AccountScheduler(study=fake, max_concurrency=2)  # type: ignore[arg-type]

    report = await scheduler.run([1, 2, 3, 4, 5, 6])

    assert len(report.reports) == 6
    assert fake.peak <= 2, f"并发峰值 {fake.peak} 超过上限"


async def test_failure_is_isolated_per_account() -> None:
    """单账号失败被隔离进 errors，其它账号照常出报告。"""
    fake = FakeStudy(fail_for={2})
    scheduler = AccountScheduler(study=fake, max_concurrency=3)  # type: ignore[arg-type]

    report = await scheduler.run([1, 2, 3])

    assert set(report.reports) == {1, 3}
    assert set(report.errors) == {2}
    assert "炸了" in report.errors[2]
    assert report.ok is False


async def test_report_aggregates_done_counts() -> None:
    """汇总能算出所有账号合计完成数（CLI 收尾用）。"""
    fake = FakeStudy()
    scheduler = AccountScheduler(study=fake, max_concurrency=2)  # type: ignore[arg-type]

    report = await scheduler.run([1, 2, 3])

    assert report.total_done() == 3
    assert report.ok is True


async def test_limits_are_passed_through() -> None:
    """课程范围与 limit 要透传给刷课服务（试跑场景）。"""
    seen: dict[str, object] = {}

    class RecordingStudy(FakeStudy):
        async def run_account(
            self,
            account_id: int,
            *,
            course_ids: Sequence[int] | None = None,
            limit: int | None = None,
        ) -> StudyReport:
            seen["course_ids"] = course_ids
            seen["limit"] = limit
            return await super().run_account(account_id, course_ids=course_ids, limit=limit)

    scheduler = AccountScheduler(study=RecordingStudy(), max_concurrency=1)  # type: ignore[arg-type]

    await scheduler.run([1], course_ids=[7, 8], limit=3)

    assert seen["course_ids"] == [7, 8]
    assert seen["limit"] == 3


async def test_empty_account_list_is_a_noop() -> None:
    """没有账号时不该报错（空列表是合法输入）。"""
    scheduler = AccountScheduler(study=FakeStudy(), max_concurrency=2)  # type: ignore[arg-type]

    report = await scheduler.run([])

    assert report.reports == {}
    assert report.ok is True
