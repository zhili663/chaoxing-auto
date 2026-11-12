"""提示词入库（`prompts` 表）—— 「改完即生效」的落点。

计划 §5.10 的零硬编码红线：提示词属于**业务数据**，不能只以字面量形式存在于代码里。
优先级是：**库里的 → 内置默认**（管理员在网页上改，不需要发版重启）。

版本号每次写入自增 —— 回答"这轮答案是哪版提示词"时不用翻日志。
"""

from __future__ import annotations

from sqlalchemy import select

from chaoxing.storage.models import Prompt
from chaoxing.storage.uow import UnitOfWork

#: 答题用系统提示词的 key（与 `answers.py` 的内置默认配套）
PROMPT_KEY_SYSTEM = "quiz.system"


class PromptService:
    """提示词的读写（唯一入口）。

    :param uow: 工作单元
    """

    def __init__(self, *, uow: UnitOfWork) -> None:
        self._uow = uow

    async def get(self, key: str) -> str | None:
        """取提示词正文。

        :param key: 提示词 key
        :returns: 正文；没写过时 ``None``（调用方据此回落内置默认）
        """
        async with self._uow.read() as db:
            row = (await db.scalars(select(Prompt).where(Prompt.key == key))).first()
            return row.content if row is not None else None

    async def list_all(self) -> list[Prompt]:
        """列出全部提示词（按 key 排序）。"""
        async with self._uow.read() as db:
            return list((await db.scalars(select(Prompt).order_by(Prompt.key))).all())

    async def upsert(
        self, key: str, content: str, *, user_id: int | None, description: str = ""
    ) -> Prompt:
        """写入提示词（不存在则建，存在则版本 +1）。

        :param key: 提示词 key
        :param content: 正文
        :param user_id: 谁改的（进审计）
        :param description: 说明（留空则不改动既有说明）
        :returns: 落库后的行（``version`` 已更新）
        """
        async with self._uow.begin() as db:
            row = (await db.scalars(select(Prompt).where(Prompt.key == key))).first()
            if row is None:
                row = Prompt(
                    key=key,
                    content=content,
                    version=1,
                    description=description,
                    updated_by=user_id,
                )
                db.add(row)
            else:
                row.content = content
                row.version += 1
                row.updated_by = user_id
                if description:
                    row.description = description
            await db.flush()
            return row


__all__ = ["PROMPT_KEY_SYSTEM", "PromptService"]
