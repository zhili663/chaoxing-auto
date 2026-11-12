"""任务点仓储。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chaoxing.storage.models import TaskPoint


class TaskPointRepo:
    """任务点的读写（进度、状态、重试计数）。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, course_id: int, point_id: str) -> TaskPoint | None:
        """按（课程, 任务点）取记录（扫描时用来区分"新增"与"更新"）。"""
        statement = select(TaskPoint).where(
            TaskPoint.course_id == course_id, TaskPoint.point_id == point_id
        )
        return (await self._session.scalars(statement)).first()

    async def list_pending(self, account_id: int, course_id: int) -> Sequence[TaskPoint]:
        """按**播放顺序**列出待处理任务点。

        顺序由 ``sequence``（章序号 × 1000 + 章内序号）决定 —— 这正是"视频后面紧跟的题
        先做完，再放下一个视频"的机制来源，不是靠调用顺序碰巧正确。

        :param account_id: 账号 ID
        :param course_id: 课程 ID
        :returns: 待处理任务点（按 sequence 升序）
        """
        statement = (
            select(TaskPoint)
            .where(
                TaskPoint.account_id == account_id,
                TaskPoint.course_id == course_id,
                TaskPoint.status.in_(("queued", "failed")),
            )
            .order_by(TaskPoint.sequence, TaskPoint.id)
        )
        return list((await self._session.scalars(statement)).all())

    async def upsert(
        self,
        *,
        account_id: int,
        course_id: int,
        point_id: str,
        job_id: str = "",
        chapter_id: str = "",
        kind: str = "unknown",
        title: str = "",
        sequence: int = 0,
        duration: float = 0.0,
        payload: dict[str, Any] | None = None,
    ) -> TaskPoint:
        """写入或更新一个任务点（**不覆盖已有的进度与状态**）。

        :returns: 落库后的任务点
        """
        statement = select(TaskPoint).where(
            TaskPoint.course_id == course_id, TaskPoint.point_id == point_id
        )
        existing = (await self._session.scalars(statement)).first()
        if existing is not None:
            existing.job_id = job_id or existing.job_id
            existing.title = title or existing.title
            existing.kind = kind if kind != "unknown" else existing.kind
            existing.sequence = sequence or existing.sequence
            existing.duration = duration or existing.duration
            if payload:
                existing.payload = {**existing.payload, **payload}
            await self._session.flush()
            return existing

        created = TaskPoint(
            account_id=account_id,
            course_id=course_id,
            point_id=point_id,
            job_id=job_id,
            chapter_id=chapter_id,
            kind=kind,
            title=title,
            sequence=sequence,
            duration=duration,
            payload=dict(payload or {}),
        )
        self._session.add(created)
        await self._session.flush()
        return created

    async def mark(
        self,
        point_id: int,
        *,
        status: str,
        progress: float | None = None,
        error: str = "",
        bump_retry: bool = False,
    ) -> bool:
        """更新状态（心跳推进、失败计数、人工介入标记都走这里）。

        :param point_id: 任务点主键
        :param status: 新状态
        :param progress: 已上报进度（秒），``None`` 表示不改
        :param error: 失败原因（非空时覆盖）
        :param bump_retry: 是否把重试计数 +1
        :returns: 是否存在并被更新
        """
        point = await self._session.get(TaskPoint, point_id)
        if point is None:
            return False
        point.status = status
        if progress is not None:
            point.progress = float(progress)
        if error:
            point.last_error = error
        if bump_retry:
            point.retry_count += 1
        await self._session.flush()
        return True

    async def status_counts(self, account_id: int) -> dict[str, int]:
        """按状态统计任务点数量（进度面板与体检用）。

        :param account_id: 账号 ID
        :returns: ``{"done": 3, "queued": 7, ...}``
        """
        statement = (
            select(TaskPoint.status, func.count())
            .where(TaskPoint.account_id == account_id)
            .group_by(TaskPoint.status)
        )
        rows = (await self._session.execute(statement)).all()
        return {str(status): int(count) for status, count in rows}
