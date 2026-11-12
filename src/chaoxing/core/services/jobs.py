"""手动任务记录（``jobs`` 表）：列出最近的、取消还在跑的那个。

任务本身由触发方（CLI / API）创建并推进；这一层只提供**可观测与可干预**两件事。
取消只允许 `queued` / `running` —— 其余状态（done / failed / cancelled）取消没有意义，
直接报 400 而不是假装成功。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from chaoxing.storage.models import Job
from chaoxing.storage.uow import UnitOfWork

#: 允许取消的状态
CANCELLABLE = ("queued", "running")


class JobNotFound(Exception):
    """任务不存在。"""


class JobNotCancellable(Exception):
    """任务处于不可取消的状态。"""


@dataclass(slots=True)
class JobView:
    """一条任务记录（脱掉 ORM）。"""

    id: int
    kind: str
    account_id: int | None
    status: str
    result: str
    started_at: datetime | None
    finished_at: datetime | None


def _to_view(job: Job) -> JobView:
    """ORM → :class:`JobView`。"""
    return JobView(
        id=job.id,
        kind=job.kind,
        account_id=job.account_id,
        status=job.status,
        result=job.result,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


class JobService:
    """任务记录读写。

    :param uow: 工作单元
    """

    def __init__(self, *, uow: UnitOfWork) -> None:
        self._uow = uow

    async def list_recent(self, *, limit: int = 100) -> list[JobView]:
        """最近的任务（新的在前）。"""
        async with self._uow.read() as db:
            statement = select(Job).order_by(Job.id.desc()).limit(max(1, min(limit, 500)))
            rows = list((await db.scalars(statement)).all())
        return [_to_view(row) for row in rows]

    async def cancel(self, job_id: int) -> JobView:
        """取消一个任务。

        :param job_id: 任务 ID
        :raises JobNotFound: 不存在
        :raises JobNotCancellable: 状态不允许取消
        :returns: 取消后的记录
        """
        async with self._uow.begin() as db:
            job = await db.get(Job, job_id)
            if job is None:
                raise JobNotFound(f"任务 {job_id} 不存在")
            if job.status not in CANCELLABLE:
                raise JobNotCancellable(f"任务处于 {job.status}，不能取消")
            job.status = "cancelled"
            job.finished_at = datetime.now(UTC)
            await db.flush()
            return _to_view(job)


__all__ = ["CANCELLABLE", "JobNotCancellable", "JobNotFound", "JobService", "JobView"]
