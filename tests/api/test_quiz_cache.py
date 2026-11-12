"""题库接口：浏览、人工修正、删除。

来源：一条业务判断 —— **人工改过的答案来源标 ``manual``，模型之后不再覆盖它**。
否则"改一次又被打回"会让人彻底放弃修正这条路。
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from chaoxing.core.container import Container
from chaoxing.storage.models import AnswerCache


def secret_of(password: str) -> str:
    """前端口径的口令。"""
    return hashlib.sha256(password.encode()).hexdigest()


def register(client: TestClient, username: str = "admin", password: str = "passw0rd") -> None:
    """注册并登录（首个用户即超管）。"""
    nonce = client.get("/api/auth/challenge", params={"scene": "register"}).json()["nonce"]
    response = client.post(
        "/api/auth/register",
        json={"username": username, "secret": secret_of(password), "nonce": nonce},
    )
    assert response.status_code == 201, response.text
    assert (
        client.post(
            "/api/auth/login", json={"username": username, "secret": secret_of(password)}
        ).status_code
        == 200
    )


def seed_answer(client: TestClient, *, fingerprint: str = "abc123", answer: str = "AB") -> None:
    """直接往题库塞一条（模拟刷题过程中的产出）。"""
    container: Container = client.app.state.container  # type: ignore[attr-defined]

    async def _insert() -> None:
        async with container.uow.begin() as db:
            db.add(
                AnswerCache(
                    question_hash=fingerprint,
                    question="下列属于危化品事故特性的是（）",
                    answer=answer,
                    source="model",
                    confidence=0.7,
                    hits=2,
                )
            )
            await db.flush()

    asyncio.run(_insert())


def read_answer(client: TestClient, fingerprint: str) -> AnswerCache | None:
    """读回一条。"""
    container: Container = client.app.state.container  # type: ignore[attr-defined]

    async def _read() -> AnswerCache | None:
        async with container.uow.read() as db:
            return (
                await db.scalars(
                    select(AnswerCache).where(AnswerCache.question_hash == fingerprint)
                )
            ).first()

    return asyncio.run(_read())


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    """已登录的超管。"""
    register(client)
    return client


@pytest.fixture
def viewer(admin: TestClient) -> Iterator[TestClient]:
    """只有 viewer 角色（能看题库，不能改）。"""
    from starlette.testclient import TestClient as StarletteClient

    with StarletteClient(admin.app) as peer:
        created = admin.post(
            "/api/users",
            json={"username": "carol", "secret": secret_of("carol-pass"), "role": "viewer"},
        )
        assert created.status_code == 200, created.text
        assert (
            peer.post(
                "/api/auth/login", json={"username": "carol", "secret": secret_of("carol-pass")}
            ).status_code
            == 200
        )
        yield peer


def test_empty_library_is_fine(admin: TestClient) -> None:
    """题库为空时返回空列表（而不是报错）。"""
    response = admin.get("/api/quiz/cache")

    assert response.status_code == 200
    assert response.json() == []


def test_list_returns_records(admin: TestClient) -> None:
    """列出题库（含来源与命中次数）。"""
    seed_answer(admin)

    response = admin.get("/api/quiz/cache")

    assert response.status_code == 200
    item = response.json()[0]
    assert item["answer"] == "AB"
    assert item["source"] == "model"
    assert item["hits"] == 2


def test_search_by_keyword(admin: TestClient) -> None:
    """按题干关键词搜索。"""
    seed_answer(admin)

    assert len(admin.get("/api/quiz/cache", params={"keyword": "危化品"}).json()) == 1
    assert admin.get("/api/quiz/cache", params={"keyword": "不存在的词"}).json() == []


def test_manual_correction_marks_source(admin: TestClient) -> None:
    """人工修正后来源变成 manual（模型不会再覆盖它）。"""
    seed_answer(admin)

    response = admin.patch("/api/quiz/cache/abc123", json={"value": "ABC"})

    assert response.status_code == 200
    assert response.json()["answer"] == "ABC"
    assert response.json()["source"] == "manual"
    stored = read_answer(admin, "abc123")
    assert stored is not None
    assert stored.source == "manual"
    assert stored.confidence == 1.0


def test_correction_of_missing_record_is_404(admin: TestClient) -> None:
    """修正不存在的指纹返回 404。"""
    response = admin.patch("/api/quiz/cache/nope", json={"value": "A"})

    assert response.status_code == 404


def test_delete_removes_record(admin: TestClient) -> None:
    """删除后题库里不再有这条（下次遇到会重新问模型）。"""
    seed_answer(admin)

    assert admin.delete("/api/quiz/cache/abc123").json()["removed"] is True
    assert admin.get("/api/quiz/cache").json() == []


def test_stats(admin: TestClient) -> None:
    """题库概览要能区分人工与模型来源。"""
    seed_answer(admin, fingerprint="a", answer="A")
    seed_answer(admin, fingerprint="b", answer="B")
    admin.patch("/api/quiz/cache/a", json={"value": "A"})

    stats = admin.get("/api/quiz/stats").json()

    assert stats["total"] == 2
    assert stats["manual"] == 1
    assert stats["model"] == 1


def test_viewer_can_read_but_not_write(viewer: TestClient, admin: TestClient) -> None:
    """viewer 能看题库，但不能改（权限节点分开粒度）。"""
    seed_answer(admin)

    assert viewer.get("/api/quiz/cache").status_code == 200
    assert viewer.patch("/api/quiz/cache/abc123", json={"value": "X"}).status_code == 403
    assert viewer.delete("/api/quiz/cache/abc123").status_code == 403
