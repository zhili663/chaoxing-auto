"""legacy 导入的真实边界：换密钥、重复导入、空库、dry-run。

来源：迁移时最容易踩的两件事 ——
① 旧库的密文是**另一个 SECRET_KEY** 加的，导进来只会得到一堆永远解不开的账号；
② 迁移脚本被跑了两次，第二次把数据翻倍。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import func, select

from chaoxing.storage.legacy_import import can_decrypt, extract_answer, import_legacy_data
from chaoxing.storage.models import Account, AnswerCache
from chaoxing.storage.uow import UnitOfWork

LEGACY_SCHEMA = """
CREATE TABLE accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_enc TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    cookie_enc TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    last_login_at TEXT, last_error TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE answer_cache (
    question_hash TEXT PRIMARY KEY,
    question_text TEXT NOT NULL DEFAULT '',
    options_json TEXT NOT NULL DEFAULT '[]',
    answer_json TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'model',
    confidence REAL NOT NULL DEFAULT 0,
    model TEXT NOT NULL DEFAULT '',
    hits INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""


def make_legacy_db(
    path: Path,
    *,
    accounts: tuple[tuple[str, str], ...] = (),
    answers: tuple[tuple[str, str, str], ...] = (),
) -> None:
    """造一个 legacy 结构的临时库。"""
    connection = sqlite3.connect(path)
    try:
        connection.executescript(LEGACY_SCHEMA)
        connection.executemany(
            "INSERT INTO accounts (username, password_enc, created_at, updated_at) "
            "VALUES (?, ?, '2026-01-01', '2026-01-01')",
            accounts,
        )
        connection.executemany(
            "INSERT INTO answer_cache (question_hash, question_text, answer_json, "
            "created_at, updated_at) VALUES (?, ?, ?, '2026-01-01', '2026-01-01')",
            answers,
        )
        connection.commit()
    finally:
        connection.close()


async def count(session: object, model: type) -> int:
    """统计某张表的行数。"""
    return int((await session.scalar(select(func.count()).select_from(model))) or 0)  # type: ignore[attr-defined]


async def test_imports_accounts_without_owner(tmp_path: Path, uow: UnitOfWork) -> None:
    """账号导入后属主必须为空（旧库没有平台用户概念）。"""
    key = Fernet.generate_key().decode()
    cipher = "enc::" + Fernet(key.encode()).encrypt(b"secret-password").decode()
    legacy = tmp_path / "chaoxing.db"
    make_legacy_db(legacy, accounts=(("13800000000", cipher),))

    async with uow.begin() as session:
        report = await import_legacy_data(legacy, session, secret_key=key)

    assert report.accounts_imported == 1
    async with uow.read() as session:
        account = (await session.scalars(select(Account))).one()
        assert account.owner_user_id is None
        assert account.username == "13800000000"
        assert account.password_enc == cipher.removeprefix("enc::")  # 前缀被剥掉，存标准 token


async def test_undecryptable_account_is_skipped_with_warning(
    tmp_path: Path, uow: UnitOfWork
) -> None:
    """换了密钥的旧密文必须被拦下来，而不是写进一堆解不开的账号。"""
    other_key = Fernet.generate_key().decode()
    cipher = "enc::" + Fernet(other_key.encode()).encrypt(b"secret").decode()
    legacy = tmp_path / "chaoxing.db"
    make_legacy_db(legacy, accounts=(("13800000000", cipher),))
    current_key = Fernet.generate_key().decode()

    async with uow.begin() as session:
        report = await import_legacy_data(legacy, session, secret_key=current_key)

    assert report.accounts_imported == 0
    assert report.accounts_undecryptable == 1
    assert any("SECRET_KEY" in warning for warning in report.warnings)
    async with uow.read() as session:
        assert await count(session, Account) == 0


async def test_second_import_skips_duplicates(tmp_path: Path, uow: UnitOfWork) -> None:
    """重复导入不能把数据翻倍。"""
    legacy = tmp_path / "chaoxing.db"
    make_legacy_db(
        legacy,
        accounts=(("13800000000", ""), ("13900000000", "")),
        answers=(("hash-1", "题干", '{"answer": "ABC"}'),),
    )

    async with uow.begin() as session:
        first = await import_legacy_data(legacy, session)
    async with uow.begin() as session:
        second = await import_legacy_data(legacy, session)

    assert first.accounts_imported == 2
    assert second.accounts_imported == 0
    assert second.accounts_skipped == 2
    async with uow.read() as session:
        assert await count(session, Account) == 2
        assert await count(session, AnswerCache) == 1


async def test_answers_are_imported_and_deduplicated(tmp_path: Path, uow: UnitOfWork) -> None:
    """题库按题干指纹去重：新库已有的指纹不覆盖，空答案条目跳过。"""
    legacy = tmp_path / "chaoxing.db"
    make_legacy_db(
        legacy,
        answers=(
            ("hash-1", "危险的化学品有哪些", '{"answer": "ABC", "confidence": 0.9}'),
            ("hash-2", "空答案", "{}"),
        ),
    )
    # 新库里已经有 hash-1（人为造的"重复"，模拟二次导入或已缓存）
    async with uow.begin() as session:
        session.add(AnswerCache(question_hash="hash-1", question="已有", answer="OLD"))

    async with uow.begin() as session:
        report = await import_legacy_data(legacy, session)

    assert report.answers_total == 2
    assert report.answers_imported == 0  # hash-1 已存在，hash-2 没有答案
    assert report.answers_skipped == 2
    async with uow.read() as session:
        assert await count(session, AnswerCache) == 1


async def test_answer_without_existing_row_is_imported(tmp_path: Path, uow: UnitOfWork) -> None:
    """没有冲突的条目要正常导入，且答案从 JSON 里取出来。"""
    legacy = tmp_path / "chaoxing.db"
    make_legacy_db(
        legacy,
        answers=(
            ("hash-1", "危险的化学品有哪些", '{"answer": "ABC", "confidence": 0.9}'),
            ("hash-2", "空答案", "{}"),
        ),
    )

    async with uow.begin() as session:
        report = await import_legacy_data(legacy, session)

    assert report.answers_imported == 1
    assert report.answers_skipped == 1
    async with uow.read() as session:
        cached = (await session.scalars(select(AnswerCache))).one()
        assert cached.answer == "ABC"


async def test_dry_run_writes_nothing(tmp_path: Path, uow: UnitOfWork) -> None:
    """``--dry-run`` 只统计（迁移前先看清楚会发生什么）。"""
    legacy = tmp_path / "chaoxing.db"
    make_legacy_db(legacy, accounts=(("13800000000", ""),))

    async with uow.begin() as session:
        report = await import_legacy_data(legacy, session, dry_run=True)

    assert report.accounts_imported == 1
    async with uow.read() as session:
        assert await count(session, Account) == 0


async def test_missing_legacy_db_is_reported(tmp_path: Path, uow: UnitOfWork) -> None:
    """旧库不存在时给出明确警告，而不是静默成功。"""
    async with uow.begin() as session:
        report = await import_legacy_data(tmp_path / "nope.db", session)

    assert report.warnings
    assert report.imported_anything is False


def test_extract_answer_handles_json_variants() -> None:
    """旧格式五花八门，取不到就退回原文。"""
    assert extract_answer('{"answer": "ABC"}') == "ABC"
    assert extract_answer('{"value": "对"}') == "对"
    assert extract_answer('"纯字符串"') == "纯字符串"
    assert extract_answer("不是 JSON 的答案") == "不是 JSON 的答案"
    assert extract_answer('{"other": 1}') == ""
    assert extract_answer("") == ""


def test_can_decrypt_without_key_is_permissive() -> None:
    """没配密钥时无从校验，放行（并在报告里靠其他字段体现）。"""
    assert can_decrypt("whatever", "") is True
    assert can_decrypt("", "some-key") is True
