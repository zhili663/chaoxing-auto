"""课程仓储。"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chaoxing.storage.base import utcnow
from chaoxing.storage.models import Course


class CourseRepo:
    """课程列表的读写（"刷什么"由网页 CRUD 决定，仓储只负责存取）。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_account(
        self, account_id: int, *, selected_only: bool = False
    ) -> Sequence[Course]:
        """列出某账号的课程（默认过滤软删）。

        :param account_id: 账号 ID
        :param selected_only: 只列已勾选要刷的
        :returns: 课程列表（按 priority 升序）
        """
        statement = select(Course).where(
            Course.account_id == account_id, Course.deleted_at.is_(None)
        )
        if selected_only:
            statement = statement.where(Course.selected.is_(True))
        statement = statement.order_by(Course.priority, Course.id)
        return list((await self._session.scalars(statement)).all())

    async def get_by_key(self, account_id: int, course_key: str) -> Course | None:
        """按 ``courseId:clazzId`` 取课程。"""
        statement = select(Course).where(
            Course.account_id == account_id, Course.course_key == course_key
        )
        return (await self._session.scalars(statement)).first()

    async def upsert_from_scan(
        self,
        account_id: int,
        *,
        course_key: str,
        title: str = "",
        teacher: str = "",
        cover: str = "",
    ) -> Course:
        """把一次扫描到的课程写库（已存在则更新标题，**不覆盖用户的勾选与优先级**）。

        仓储只收基本类型 —— 把 ``client`` 层的模型转成这几个字段是 ``core`` 的活
        （分层契约：storage 不得依赖 client）。

        :param account_id: 账号 ID
        :param course_key: ``courseId:clazzId``
        :returns: 落库后的课程对象
        """
        existing = await self.get_by_key(account_id, course_key)
        if existing is not None:
            existing.title = title or existing.title
            existing.teacher = teacher or existing.teacher
            existing.cover = cover or existing.cover
            existing.scanned_at = utcnow()
            existing.deleted_at = None
            await self._session.flush()
            return existing

        created = Course(
            account_id=account_id,
            course_key=course_key,
            title=title,
            teacher=teacher,
            cover=cover,
            scanned_at=utcnow(),
        )
        self._session.add(created)
        await self._session.flush()
        return created

    async def set_selected(self, course_id: int, *, selected: bool) -> bool:
        """勾选 / 取消勾选。"""
        course = await self._session.get(Course, course_id)
        if course is None:
            return False
        course.selected = selected
        await self._session.flush()
        return True

    async def soft_delete(self, course_id: int) -> bool:
        """软删（保留历史统计）。"""
        course = await self._session.get(Course, course_id)
        if course is None:
            return False
        course.deleted_at = utcnow()
        course.selected = False
        await self._session.flush()
        return True
