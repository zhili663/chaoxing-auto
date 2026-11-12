"""刷课计划服务：计划 CRUD。

``study_plans`` + ``study_plan_courses`` 是"刷什么"的**唯一权威来源** ——
课程名或 ID 绝不出现在代码里（零硬编码原则，见 ``docs/REFACTOR_PLAN.md`` §5.10）。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete, select

from chaoxing.storage.models import Course, StudyPlan, StudyPlanCourse
from chaoxing.storage.uow import UnitOfWork

log = logging.getLogger(__name__)

#: 默认策略（不含任何业务数据，只是"开关的初值"）
DEFAULT_STRATEGY: dict[str, Any] = {
    "video": True,
    "quiz": True,
    "document": True,
    "exam": False,  # 考试默认不动
    "rate": 2.0,
    "order": "sequence",
}


class PlanNotFound(Exception):
    """计划不存在。"""


class PlanService:
    """计划的增删改查。

    :param uow: 工作单元
    """

    def __init__(self, *, uow: UnitOfWork) -> None:
        self._uow = uow

    async def create(
        self,
        *,
        account_id: int,
        name: str = "",
        course_keys: list[str] | None = None,
        strategy: dict[str, Any] | None = None,
        user_id: int | None = None,
    ) -> StudyPlan:
        """新建计划。

        :param account_id: 归属账号
        :param name: 计划名（空则自动生成）
        :param course_keys: 计划包含的课程（空 = 该账号全部已勾选的课程）
        :param strategy: 策略覆盖（与 :data:`DEFAULT_STRATEGY` 合并）
        :param user_id: 归属用户（可空）
        :returns: 新建的计划
        """
        merged = {**DEFAULT_STRATEGY, **(strategy or {})}
        async with self._uow.begin() as db:
            plan = StudyPlan(
                account_id=account_id,
                user_id=user_id,
                name=name or f"account-{account_id}-plan",
                strategy=merged,
            )
            db.add(plan)
            await db.flush()

            keys = course_keys or await self._selected_keys(db, account_id)
            for index, course_key in enumerate(keys):
                course_id = await self._course_id(db, account_id, course_key)
                if course_id is None:
                    continue
                db.add(StudyPlanCourse(plan_id=plan.id, course_id=course_id, sequence=index))
            await db.flush()
        log.info("已创建计划 #%s（账号 %s，%d 门课）", plan.id, account_id, len(keys))
        return plan

    async def list_plans(self, *, account_id: int | None = None) -> list[StudyPlan]:
        """列出计划。"""
        async with self._uow.read() as db:
            statement = select(StudyPlan).order_by(StudyPlan.id)
            if account_id is not None:
                statement = statement.where(StudyPlan.account_id == account_id)
            return list((await db.scalars(statement)).all())

    async def get(self, plan_id: int) -> StudyPlan:
        """取计划。

        :raises PlanNotFound: 不存在
        """
        async with self._uow.read() as db:
            plan = await db.get(StudyPlan, plan_id)
            if plan is None:
                raise PlanNotFound(f"计划 {plan_id} 不存在")
            return plan

    async def course_ids(self, plan_id: int) -> list[int]:
        """计划包含的课程（按执行顺序）。"""
        async with self._uow.read() as db:
            rows = list(
                (
                    await db.scalars(
                        select(StudyPlanCourse)
                        .where(StudyPlanCourse.plan_id == plan_id)
                        .order_by(StudyPlanCourse.sequence, StudyPlanCourse.id)
                    )
                ).all()
            )
        return [row.course_id for row in rows]

    async def set_enabled(self, plan_id: int, *, enabled: bool) -> bool:
        """启用 / 停用计划。"""
        async with self._uow.begin() as db:
            plan = await db.get(StudyPlan, plan_id)
            if plan is None:
                return False
            plan.enabled = enabled
            await db.flush()
            return True

    async def delete(self, plan_id: int) -> bool:
        """删除计划（课程关联由外键级联清理）。"""
        async with self._uow.begin() as db:
            plan = await db.get(StudyPlan, plan_id)
            if plan is None:
                return False
            await db.execute(delete(StudyPlanCourse).where(StudyPlanCourse.plan_id == plan_id))
            await db.delete(plan)
            await db.flush()
            return True

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    @staticmethod
    async def _selected_keys(db: Any, account_id: int) -> list[str]:
        """该账号已勾选的课程键（计划未指定课程时的默认集合）。"""
        rows = list(
            (
                await db.scalars(
                    select(Course)
                    .where(
                        Course.account_id == account_id,
                        Course.selected.is_(True),
                        Course.deleted_at.is_(None),
                    )
                    .order_by(Course.priority, Course.id)
                )
            ).all()
        )
        return [row.course_key for row in rows]

    @staticmethod
    async def _course_id(db: Any, account_id: int, course_key: str) -> int | None:
        """按课程键取本地课程 ID。"""
        course = (
            await db.scalars(
                select(Course).where(
                    Course.account_id == account_id, Course.course_key == course_key
                )
            )
        ).first()
        return course.id if course is not None else None
