"""``users.phone``：可空唯一的手机号列。

抓的是两种最容易踩的事故：

* **空串 vs NULL** —— 不填手机号必须是 ``NULL``。若写成空串，第二个不填手机号的
  用户就会撞唯一索引，注册直接 500；
* **撞号要报错** —— 两个账号共用同一个手机号必须被数据库挡下（登录标识不能有歧义）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from chaoxing.storage.models import User
from chaoxing.storage.uow import UnitOfWork


async def test_multiple_users_without_phone_coexist(uow: UnitOfWork) -> None:
    """不填手机号的用户可以有很多个（NULL 不参与唯一约束）。"""
    async with uow.begin() as session:
        session.add(User(username="alice"))
        session.add(User(username="bob"))
        await session.flush()

    async with uow.read() as session:
        phones = list((await session.scalars(select(User.phone).order_by(User.id))).all())

    assert phones == [None, None]


async def test_duplicate_non_null_phone_is_rejected(uow: UnitOfWork) -> None:
    """两个账号占同一个手机号必须报 IntegrityError（不能悄悄建出一个歧义标识）。"""
    async with uow.begin() as session:
        session.add(User(username="alice", phone="13900000000"))
        await session.flush()

    with pytest.raises(IntegrityError):
        async with uow.begin() as session:
            session.add(User(username="bob", phone="13900000000"))
            await session.flush()
