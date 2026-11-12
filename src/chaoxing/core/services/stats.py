"""统计服务：按天聚合（热力图与 KPI 的数据源）。

两个设计决定：

1. **累加用数据库原子 UPSERT**（``INSERT … ON CONFLICT DO UPDATE``），而不是
   "先查再改" —— 多个账号并发刷课时，后者会丢更新（读到旧值、都加一、写回同一个数）；
2. **"天"按本地时区算** —— 统计是给人看的，"今天刷了多少"必须跟用户看到的日历一致，
   而不是 UTC 的日期切换。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from chaoxing.storage.models import Account, ActivityDaily, Course, TaskPoint
from chaoxing.storage.uow import UnitOfWork

log = logging.getLogger(__name__)


def local_day(when: date | None = None) -> str:
    """本地时区的日期字符串（``YYYY-MM-DD``）。"""
    target = when or date.today()
    return target.strftime("%Y-%m-%d")


@dataclass(slots=True)
class DailyStat:
    """一天的活动。"""

    day: str
    points_done: int = 0
    quiz_done: int = 0
    watched_seconds: float = 0.0
    failed_count: int = 0

    @property
    def watched_minutes(self) -> float:
        """观看时长（分钟，展示用）。"""
        return round(self.watched_seconds / 60, 1)


@dataclass(slots=True)
class UserStat:
    """一个属主的聚合（``owner_user_id=None`` = **共享账号**）。"""

    owner_user_id: int | None
    accounts: int
    points_done: int


class StatsService:
    """按天统计的读写。

    :param uow: 工作单元
    """

    def __init__(self, *, uow: UnitOfWork) -> None:
        self._uow = uow

    # ------------------------------------------------------------------ #
    # 写（事件驱动）
    # ------------------------------------------------------------------ #
    async def bump(
        self,
        account_id: int,
        *,
        points: int = 0,
        quizzes: int = 0,
        watched_seconds: float = 0.0,
        failed: int = 0,
        day: str | None = None,
    ) -> None:
        """给"账号 × 今天"累加计数（原子操作，并发安全）。

        :param account_id: 账号 ID
        :param points: 完成的任务点数增量
        :param quizzes: 完成的测验数增量
        :param watched_seconds: 观看时长（秒）增量
        :param failed: 失败数增量
        :param day: 指定日期（默认今天，本地时区）
        """
        target_day = day or local_day()
        statement = sqlite_insert(ActivityDaily).values(
            account_id=account_id,
            day=target_day,
            points_done=points,
            quiz_done=quizzes,
            watched_seconds=watched_seconds,
            failed_count=failed,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[ActivityDaily.account_id, ActivityDaily.day],
            set_={
                "points_done": ActivityDaily.points_done + points,
                "quiz_done": ActivityDaily.quiz_done + quizzes,
                "watched_seconds": ActivityDaily.watched_seconds + watched_seconds,
                "failed_count": ActivityDaily.failed_count + failed,
            },
        )
        async with self._uow.begin() as session:
            await session.execute(statement)

    # ------------------------------------------------------------------ #
    # 读
    # ------------------------------------------------------------------ #
    async def daily(self, *, account_id: int | None = None, days: int = 30) -> list[DailyStat]:
        """最近 ``days`` 天的按天统计（缺的日期补 0，便于画热力图）。

        :param account_id: 只看某账号（``None`` = 全部账号合计）
        :param days: 天数
        :returns: 按日期升序的列表
        """
        since = local_day(date.today() - timedelta(days=days - 1))
        async with self._uow.read() as session:
            statement = (
                select(
                    ActivityDaily.day,
                    func.sum(ActivityDaily.points_done),
                    func.sum(ActivityDaily.quiz_done),
                    func.sum(ActivityDaily.watched_seconds),
                    func.sum(ActivityDaily.failed_count),
                )
                .where(ActivityDaily.day >= since)
                .group_by(ActivityDaily.day)
                .order_by(ActivityDaily.day)
            )
            if account_id is not None:
                statement = statement.where(ActivityDaily.account_id == account_id)
            rows = (await session.execute(statement)).all()

        found = {
            str(day): DailyStat(
                day=str(day),
                points_done=int(points or 0),
                quiz_done=int(quizzes or 0),
                watched_seconds=float(watched or 0.0),
                failed_count=int(failed or 0),
            )
            for day, points, quizzes, watched, failed in rows
        }

        # 补齐空白天（前端不用自己补，图表也不会断）
        filled: list[DailyStat] = []
        for offset in range(days):
            key = local_day(date.today() - timedelta(days=days - 1 - offset))
            filled.append(found.get(key, DailyStat(day=key)))
        return filled

    async def kpi(self, *, account_id: int | None = None) -> dict[str, float | int]:
        """全局 KPI（仪表盘顶部那几个数）。

        :param account_id: 只看某账号（``None`` = 全部）
        :returns: 课程数、已选课程数、任务点分布、累计完成数与观看时长
        """
        courses = select(func.count()).select_from(Course).where(Course.deleted_at.is_(None))
        selected = (
            select(func.count())
            .select_from(Course)
            .where(Course.deleted_at.is_(None), Course.selected.is_(True))
        )
        points = select(TaskPoint.status, func.count()).group_by(TaskPoint.status)
        watched = select(func.coalesce(func.sum(ActivityDaily.watched_seconds), 0.0))
        quizzes = select(func.coalesce(func.sum(ActivityDaily.quiz_done), 0))

        if account_id is not None:
            courses = courses.where(Course.account_id == account_id)
            selected = selected.where(Course.account_id == account_id)
            points = points.where(TaskPoint.account_id == account_id)
            watched = watched.where(ActivityDaily.account_id == account_id)
            quizzes = quizzes.where(ActivityDaily.account_id == account_id)

        async with self._uow.read() as session:
            course_count = int((await session.scalar(courses)) or 0)
            selected_count = int((await session.scalar(selected)) or 0)
            status_rows = (await session.execute(points)).all()
            watched_seconds = float((await session.scalar(watched)) or 0.0)
            quiz_count = int((await session.scalar(quizzes)) or 0)

        by_status = {str(status): int(count) for status, count in status_rows}
        return {
            "courses": course_count,
            "selected_courses": selected_count,
            "points_total": sum(by_status.values()),
            "points_done": by_status.get("done", 0),
            "points_failed": by_status.get("failed", 0),
            "points_need_human": by_status.get("need_human", 0),
            "points_queued": by_status.get("queued", 0),
            "watched_minutes": round(watched_seconds / 60, 1),
            "quizzes_done": quiz_count,
        }

    async def overview(
        self, *, account_id: int | None, owner_user_id: int | None = None
    ) -> dict[str, Any]:
        """仪表盘顶部的全部数据：KPI + 今日活动 + 可见账号数。

        :param account_id: 只看某账号（``None`` = 全部）
        :param owner_user_id: 账号计数的范围（``None`` = 不限，管理员视角）
        :returns: ``{"kpi": ..., "today": ..., "accounts": ...}``
        """
        kpi = await self.kpi(account_id=account_id)
        daily = await self.daily(account_id=account_id, days=1)
        today = daily[-1] if daily else None

        statement = select(func.count()).select_from(Account)
        if owner_user_id is not None:
            statement = statement.where(Account.owner_user_id == owner_user_id)
        async with self._uow.read() as session:
            accounts = int((await session.scalar(statement)) or 0)

        return {
            "kpi": kpi,
            "today": (
                None
                if today is None
                else {
                    "day": today.day,
                    "points_done": today.points_done,
                    "quiz_done": today.quiz_done,
                    "watched_minutes": today.watched_minutes,
                    "failed_count": today.failed_count,
                }
            ),
            "accounts": accounts,
        }

    async def by_user(self) -> list[UserStat]:
        """按属主聚合（跨账号视图，只有管理员该看）。

        :returns: 每个属主一行；``owner_user_id=None`` 那行是**共享账号**
        """
        accounts = (
            select(Account.owner_user_id, func.count())
            .select_from(Account)
            .group_by(Account.owner_user_id)
        )
        done = (
            select(Account.owner_user_id, func.count())
            .select_from(TaskPoint)
            .join(Account, Account.id == TaskPoint.account_id)
            .where(TaskPoint.status == "done")
            .group_by(Account.owner_user_id)
        )
        async with self._uow.read() as session:
            account_rows = dict((await session.execute(accounts)).all())
            done_rows = dict((await session.execute(done)).all())

        owners = sorted(
            set(account_rows) | set(done_rows), key=lambda item: (item is not None, item or 0)
        )
        return [
            UserStat(
                owner_user_id=owner,
                accounts=int(account_rows.get(owner, 0)),
                points_done=int(done_rows.get(owner, 0)),
            )
            for owner in owners
        ]
