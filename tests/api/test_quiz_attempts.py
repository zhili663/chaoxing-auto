"""刷题记录接口（`GET /quiz/attempts`）：形状、排序、分页上限与可见性。

来源：真交卷会往 `quiz_attempts` 写行（一次交卷一行），但此前只有题库缓存与概览两个端点，
网页端看不到"每一次交卷"。这里只测只读列表 —— 没有写路径，记录只能由刷课过程产生。

口径：数据范围走 `chaoxing.api.scoping` 的共用口径 —— 普通用户必须指定**自己的**账号
（不指定 → 400 `account_required`），指定别人的账号按 **404** 处理
（403 等于确认"这个账号存在"，把存在性变成可探测信息）。
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chaoxing.core.container import Container
from chaoxing.storage.models import Course, QuizAttempt

#: 响应字段（模型列 + `account_label` / `course_title` 两个展示字段）—— 多一个少一个都算接口漂移
FIELDS = {
    "account_id",
    "account_label",
    "answered_count",
    "course_id",
    "course_title",
    "created_at",
    "detail",
    "id",
    "question_count",
    "reply",
    "submitted",
    "task_point_id",
    "work_id",
}


def secret_of(password: str) -> str:
    """前端口径的口令。"""
    return hashlib.sha256(password.encode()).hexdigest()


def login(client: TestClient, username: str, password: str) -> None:
    """登录（会话进 Cookie jar）。"""
    response = client.post(
        "/api/auth/login", json={"username": username, "secret": secret_of(password)}
    )
    assert response.status_code == 200, response.text


def register(client: TestClient, username: str = "admin", password: str = "passw0rd") -> None:
    """注册并登录（首个用户即超管）。"""
    nonce = client.get("/api/auth/challenge", params={"scene": "register"}).json()["nonce"]
    response = client.post(
        "/api/auth/register",
        json={"username": username, "secret": secret_of(password), "nonce": nonce},
    )
    assert response.status_code == 201, response.text
    login(client, username, password)


def create_account(client: TestClient, *, username: str, owner_user_id: int | None = None) -> int:
    """建一个账号（默认归创建者；显式传属主用来造"别人的账号"）。"""
    payload: dict[str, Any] = {"username": username}
    if owner_user_id is not None:
        payload["owner_user_id"] = owner_user_id
    response = client.post("/api/accounts", json=payload)
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def seed_course(
    client: TestClient, *, account_id: int, title: str = "大学英语", deleted: bool = False
) -> int:
    """直接塞一门课（课程名只在 DB 里 —— 端点要自己把它补进记录）。"""
    container: Container = client.app.state.container  # type: ignore[attr-defined]

    async def _insert() -> int:
        async with container.uow.begin() as db:
            course = Course(
                account_id=account_id,
                course_key=f"course:{account_id}:{title}",
                title=title,
                deleted_at=datetime.now(UTC) if deleted else None,
            )
            db.add(course)
            await db.flush()
            return course.id

    return asyncio.run(_insert())


def seed_attempt(
    client: TestClient,
    *,
    account_id: int,
    work_id: str = "9001",
    course_id: int | None = None,
    created_at: datetime | None = None,
    question_count: int = 6,
    answered_count: int = 5,
    submitted: bool = True,
    reply: str = "提交成功",
    detail: dict[str, Any] | None = None,
) -> None:
    """直接塞一条交卷记录（模拟刷课过程中的产出）。"""
    container: Container = client.app.state.container  # type: ignore[attr-defined]

    async def _insert() -> None:
        async with container.uow.begin() as db:
            db.add(
                QuizAttempt(
                    account_id=account_id,
                    course_id=course_id,
                    task_point_id=None,
                    work_id=work_id,
                    question_count=question_count,
                    answered_count=answered_count,
                    submitted=submitted,
                    reply=reply,
                    detail={"confidence": 0.7} if detail is None else detail,
                    **({} if created_at is None else {"created_at": created_at}),
                )
            )
            await db.flush()

    asyncio.run(_insert())


def only(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """取唯一一条（多于 / 少于一条都是用例自己写错了）。"""
    assert len(rows) == 1, rows
    return rows[0]


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    """已登录的超管。"""
    register(client)
    return client


@pytest.fixture
def viewer(admin: TestClient) -> Iterator[TestClient]:
    """只有 viewer 角色（能读题库，不能读别人的账号）。"""
    from starlette.testclient import TestClient as StarletteClient

    with StarletteClient(admin.app) as peer:
        created = admin.post(
            "/api/users",
            json={"username": "carol", "secret": secret_of("carol-pass"), "role": "viewer"},
        )
        assert created.status_code == 200, created.text
        login(peer, "carol", "carol-pass")
        yield peer


@pytest.fixture
def viewer_id(viewer: TestClient) -> int:
    """普通用户自己的用户 ID（造"他自己的账号"时要用）。"""
    return int(viewer.get("/api/auth/me").json()["id"])


def test_empty_list_is_fine(admin: TestClient) -> None:
    """一条记录都没有时返回空列表（而不是报错）—— 页面据此显示空态。"""
    response = admin.get("/api/quiz/attempts")

    assert response.status_code == 200
    assert response.json() == []


def test_shape_is_model_row_plus_labels(admin: TestClient) -> None:
    """形状：模型字段 + `account_label`（脱敏）/ `course_title`（从课程表补），不多不少。"""
    account_id = create_account(admin, username="13800001234")
    course_id = seed_course(admin, account_id=account_id, title="大学英语")
    seed_attempt(admin, account_id=account_id, course_id=course_id, work_id="9001")

    item = only(admin.get("/api/quiz/attempts", params={"account_id": account_id}).json())

    assert set(item) == FIELDS
    assert item["account_id"] == account_id
    assert item["account_label"] == "138****1234"
    assert item["course_id"] == course_id
    assert item["course_title"] == "大学英语"
    assert item["work_id"] == "9001"
    assert item["question_count"] == 6
    assert item["answered_count"] == 5
    assert item["submitted"] is True
    assert item["reply"] == "提交成功"
    assert item["detail"] == {"confidence": 0.7}
    assert item["created_at"]


def test_short_username_is_masked_too(admin: TestClient) -> None:
    """非手机号账号（学号）同样脱敏 —— 学号也是身份信息，没有理由铺在列表里。"""
    account_id = create_account(admin, username="20240101001")
    seed_attempt(admin, account_id=account_id)

    item = only(admin.get("/api/quiz/attempts", params={"account_id": account_id}).json())

    assert item["account_label"] == "20****01"


def test_course_fields_are_null_when_absent(admin: TestClient) -> None:
    """课程可空：真写入路径（`StudyService._record_attempt`）不落课程，页面不能因此崩。"""
    account_id = create_account(admin, username="13700000001")
    seed_attempt(admin, account_id=account_id, work_id="9100")

    item = only(admin.get("/api/quiz/attempts", params={"account_id": account_id}).json())

    assert item["course_id"] is None
    assert item["course_title"] is None


def test_deleted_course_still_shows_its_title(admin: TestClient) -> None:
    """课程被软删也要能回显课程名：历史记录的名字不该跟着消失。"""
    account_id = create_account(admin, username="13700000002")
    course_id = seed_course(admin, account_id=account_id, title="线性代数", deleted=True)
    seed_attempt(admin, account_id=account_id, course_id=course_id, work_id="9101")

    item = only(admin.get("/api/quiz/attempts", params={"account_id": account_id}).json())

    assert item["course_title"] == "线性代数"


def test_ordered_by_created_at_desc_and_paginated(admin: TestClient) -> None:
    """按交卷时间倒序（新的在前）；`offset` 从这条序列里跳过。"""
    account_id = create_account(admin, username="13600000001")
    base = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
    for index, work_id in enumerate(["old", "middle", "new"]):
        seed_attempt(
            admin,
            account_id=account_id,
            work_id=work_id,
            created_at=base + timedelta(hours=index),
        )

    params = {"account_id": account_id}
    assert [row["work_id"] for row in admin.get("/api/quiz/attempts", params=params).json()] == [
        "new",
        "middle",
        "old",
    ]

    second = admin.get("/api/quiz/attempts", params={**params, "offset": 1, "limit": 1}).json()
    assert [row["work_id"] for row in second] == ["middle"]


def test_limit_is_clamped_to_200(admin: TestClient) -> None:
    """`limit` 超过 200 **夹紧**而不是 422：参数是"我想要多少"，不是必须改正的错误。

    与 `/quiz/cache`、`/system/tasks` 的既有口径一致（都在服务层 `min(limit, 上限)`）。
    夹紧的方向是"保留最新的"—— 记录页关心的永远是最近这批。
    """
    account_id = create_account(admin, username="13600000002")
    base = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
    for index in range(205):
        seed_attempt(
            admin,
            account_id=account_id,
            work_id=f"w{index}",
            created_at=base + timedelta(seconds=index),
        )

    rows = admin.get("/api/quiz/attempts", params={"account_id": account_id, "limit": 1000}).json()

    assert len(rows) == 200
    assert rows[0]["work_id"] == "w204"
    assert rows[-1]["work_id"] == "w5"
    assert len(admin.get("/api/quiz/attempts", params={"account_id": account_id}).json()) == 100


def test_limit_below_one_is_clamped(admin: TestClient) -> None:
    """`limit` 下限夹到 1：0 或负数不该变成"返回全表"。"""
    account_id = create_account(admin, username="13600000003")
    seed_attempt(admin, account_id=account_id, work_id="w0")
    seed_attempt(admin, account_id=account_id, work_id="w1")

    rows = admin.get("/api/quiz/attempts", params={"account_id": account_id, "limit": -5}).json()

    assert len(rows) == 1


def test_course_filter(admin: TestClient) -> None:
    """`course_id` 只收窄返回集合，不改变顺序与形状。"""
    account_id = create_account(admin, username="13500000001")
    first = seed_course(admin, account_id=account_id, title="第一门课")
    second = seed_course(admin, account_id=account_id, title="第二门课")
    seed_attempt(admin, account_id=account_id, course_id=first, work_id="in-first")
    seed_attempt(admin, account_id=account_id, course_id=second, work_id="in-second")

    rows = admin.get(
        "/api/quiz/attempts", params={"account_id": account_id, "course_id": first}
    ).json()

    assert [row["work_id"] for row in rows] == ["in-first"]


def test_foreign_account_is_404(viewer: TestClient, admin: TestClient) -> None:
    """普通用户指定别人的账号 → 404（403 等于确认这个账号存在）。"""
    account_id = create_account(admin, username="13900000000")
    seed_attempt(admin, account_id=account_id)

    response = viewer.get("/api/quiz/attempts", params={"account_id": account_id})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "account_not_found"


def test_non_admin_without_account_is_rejected(viewer: TestClient) -> None:
    """普通用户不指定账号 = 明确报错（否则"不传参数"等于把全库记录送出去）。"""
    response = viewer.get("/api/quiz/attempts")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "account_required"


def test_viewer_sees_only_own_account(
    viewer: TestClient, admin: TestClient, viewer_id: int
) -> None:
    """普通用户看自己的账号：只拿到自己那条，别人的记录不在结果里。"""
    mine = create_account(admin, username="13000000001", owner_user_id=viewer_id)
    theirs = create_account(admin, username="13000000002")
    seed_attempt(admin, account_id=mine, work_id="mine")
    seed_attempt(admin, account_id=theirs, work_id="theirs")

    rows = viewer.get("/api/quiz/attempts", params={"account_id": mine}).json()

    assert [row["work_id"] for row in rows] == ["mine"]


def test_admin_without_account_sees_everything(admin: TestClient) -> None:
    """管理员不指定账号 = 全局视图（两个账号的记录都在，新的在前）。"""
    base = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    first = create_account(admin, username="13100000001")
    second = create_account(admin, username="13100000002")
    seed_attempt(admin, account_id=first, work_id="older", created_at=base)
    seed_attempt(admin, account_id=second, work_id="newer", created_at=base + timedelta(hours=1))

    rows = admin.get("/api/quiz/attempts").json()

    assert [row["work_id"] for row in rows] == ["newer", "older"]
