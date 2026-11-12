"""仓储层：按聚合根拆分，**ORM 不出本层**。

约定：仓储只接 :class:`~sqlalchemy.ext.asyncio.AsyncSession`（由
:class:`~chaoxing.storage.uow.UnitOfWork` 提供），自己不建连接、不管事务 ——
事务边界只有一个地方（UoW），排障时不必满仓找 commit。
"""

from __future__ import annotations

from chaoxing.storage.repositories.accounts import AccountRepo
from chaoxing.storage.repositories.courses import CourseRepo
from chaoxing.storage.repositories.task_points import TaskPointRepo

__all__ = ["AccountRepo", "CourseRepo", "TaskPointRepo"]
