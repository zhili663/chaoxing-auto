"""用户服务：用「手机号或用户名」认证、建号带手机号。

登录标识的解析顺序是这里最关键的不变量 —— 先手机号、再用户名，两者都不中才失败；
并且手机号撞车必须显式报 ``UserExists``，不能悄悄覆盖出一个歧义账号。
"""

from __future__ import annotations

import hashlib

import pytest

from chaoxing.core.services import UserExists, UserService
from chaoxing.storage.uow import UnitOfWork


def secret(password: str) -> str:
    """前端口径的口令：``sha256(password)``。"""
    return hashlib.sha256(password.encode()).hexdigest()


@pytest.fixture
def service(uow: UnitOfWork) -> UserService:
    """绑定到临时库的用户服务。"""
    return UserService(uow=uow)


async def test_authenticate_by_username_still_works(service: UserService) -> None:
    """回归：没有手机号时，用户名登录必须照旧可用。"""
    await service.create(username="alice", client_secret=secret("pw123456"))

    user = await service.authenticate("alice", secret("pw123456"))
    assert user is not None
    assert user.username == "alice"
    assert user.phone is None

    assert await service.authenticate("alice", secret("wrong")) is None


async def test_authenticate_by_phone(service: UserService) -> None:
    """填了手机号后，用手机号也能登录（返回同一个账号）。"""
    await service.create(username="alice", client_secret=secret("pw123456"), phone="13900000000")

    user = await service.authenticate("13900000000", secret("pw123456"))
    assert user is not None
    assert user.username == "alice"


async def test_phone_takes_priority_over_numeric_username(service: UserService) -> None:
    """纯数字用户名（学号）与手机号并存时，手机号优先命中。"""
    await service.create(username="13900000000", client_secret=secret("oldpass1"))
    await service.create(username="bob", client_secret=secret("newpass1"), phone="13900000000")

    user = await service.authenticate("13900000000", secret("newpass1"))
    assert user is not None
    assert user.username == "bob"


async def test_numeric_username_falls_back_when_no_phone_matches(service: UserService) -> None:
    """11 位数字与任何手机号都不匹配、却与某个用户名相同 → 仍能登录（回退到用户名）。"""
    await service.create(username="13800000000", client_secret=secret("pw123456"))

    user = await service.authenticate("13800000000", secret("pw123456"))
    assert user is not None
    assert user.username == "13800000000"


async def test_duplicate_phone_on_create_raises(service: UserService) -> None:
    """手机号已被占用时建号必须报错（不能建出第二个同号账号）。"""
    await service.create(username="alice", client_secret=secret("pw123456"), phone="13900000000")

    with pytest.raises(UserExists):
        await service.create(username="bob", client_secret=secret("pw123456"), phone="13900000000")


async def test_blank_phone_is_stored_as_null(service: UserService) -> None:
    """空串/空白手机号落库要变成 ``NULL``（两个不填手机号的账号不该互相冲突）。"""
    first = await service.create(username="alice", client_secret=secret("pw123456"), phone="")
    second = await service.create(username="bob", client_secret=secret("pw123456"), phone="   ")

    assert first.phone is None
    assert second.phone is None


async def test_set_phone_updates_login_identifier(service: UserService) -> None:
    """``set_phone`` 改号后，新号能登录、旧号失效；空白可以清除。"""
    user = await service.create(username="alice", client_secret=secret("pw123456"))
    assert await service.set_phone(user.id, "13900000000") is True

    assert (await service.authenticate("13900000000", secret("pw123456"))).username == "alice"

    await service.set_phone(user.id, None)
    assert await service.authenticate("13900000000", secret("pw123456")) is None
    assert (await service.authenticate("alice", secret("pw123456"))).username == "alice"


async def test_set_phone_rejects_taken_number(service: UserService) -> None:
    """改号撞到别人的号码时同样报 ``UserExists``。"""
    await service.create(username="alice", client_secret=secret("pw123456"), phone="13900000000")
    bob = await service.create(username="bob", client_secret=secret("pw123456"))

    with pytest.raises(UserExists):
        await service.set_phone(bob.id, "13900000000")
