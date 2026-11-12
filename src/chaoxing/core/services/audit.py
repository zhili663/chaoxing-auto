"""审计查询（读侧）。

写侧在 ``api.middleware``（每个写操作自动留痕），这里只负责查 ——
把它放在 core 而不是路由里，是因为"能查什么、上限多少"属于业务规则，
不该跟 HTTP 细节混在一起。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import distinct, select

from chaoxing.storage.models import AuditLog
from chaoxing.storage.uow import UnitOfWork

#: 单次查询上限（审计表长期增长，不设上限等于把整库拖进内存）
MAX_LIMIT = 500


class AuditService:
    """审计记录的读侧。

    :param uow: 工作单元
    """

    def __init__(self, *, uow: UnitOfWork) -> None:
        self._uow = uow

    async def list(
        self,
        *,
        action: str | None = None,
        result: str | None = None,
        user_id: int | None = None,
        limit: int = 100,
    ) -> Sequence[AuditLog]:
        """按条件查审计（时间倒序）。

        :param action: 动作码（``accounts.create`` 这类）
        :param result: ok / denied / error
        :param user_id: 只看某人的操作
        :param limit: 返回条数（会被压到 :data:`MAX_LIMIT` 以内）
        :returns: 审计行
        """
        statement = select(AuditLog).order_by(AuditLog.id.desc()).limit(_clamp(limit))
        if action:
            statement = statement.where(AuditLog.action == action)
        if result:
            statement = statement.where(AuditLog.result == result)
        if user_id is not None:
            statement = statement.where(AuditLog.user_id == user_id)

        async with self._uow.read() as session:
            return list((await session.scalars(statement)).all())

    async def actions(self) -> Sequence[str]:
        """出现过的动作码（前端渲染筛选下拉用）。

        返回类型写 ``Sequence`` 而不是 ``list``：同一个类里已经有个叫 ``list``
        的方法，用 ``list[str]`` 会被解析成那个方法（mypy 会直接报错）。
        """
        async with self._uow.read() as session:
            rows = await session.execute(
                select(distinct(AuditLog.action)).order_by(AuditLog.action)
            )
        return [str(value) for value in rows.scalars().all()]


def _clamp(limit: int) -> int:
    """把条数夹到合理区间。"""
    return max(1, min(limit, MAX_LIMIT))
