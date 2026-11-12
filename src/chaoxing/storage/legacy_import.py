"""legacy 数据导入（旧库 → 新库）。

迁移原则（``docs/REFACTOR_PLAN.md`` §8.1）:

* **只迁两样**：账号（``accounts``）与题库缓存（``answer_cache``）；
* 课程与任务点**不迁** —— 学习通是唯一真相源，``cx course scan`` 重扫一遍更干净；
* 进度快照与事件流丢弃（历史价值低，新表结构不同）；
* 账号的属主**留空**：旧库没有平台用户概念，绑定由管理员在网页上按需建立；
* 凭据沿用同一个 ``SECRET_KEY`` —— 因此导入前会**逐个尝试解密**：解不开的账号
  宁可跳过并报警，也不要写进一堆以后永远取不回密码的账号。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chaoxing.storage.models import Account, AnswerCache

log = logging.getLogger(__name__)

#: legacy 给密文加的自定义前缀（``enc::<fernet token>``），用于区分"是否已加密"
ENCRYPTED_PREFIX = "enc::"
#: Fernet token 的 base64 前缀（版本字节 0x80 编码后即以它开头）
FERNET_TOKEN_PREFIX = "gAAAAA"


def normalize_ciphertext(value: str) -> str:
    """剥掉 legacy 的 ``enc::`` 前缀，归一化成标准 Fernet token。

    旧库存的不是裸 token —— 直接拿去解密必然 ``InvalidToken``，
    这正是首次 dry-run 时"两个账号都解不开"的原因。

    :param value: 旧库里的字段原文
    :returns: 归一化后的密文（无前缀则原样返回）
    """
    text = (value or "").strip()
    if text.startswith(ENCRYPTED_PREFIX):
        return text[len(ENCRYPTED_PREFIX) :]
    return text


def looks_like_fernet_token(value: str) -> bool:
    """粗判是否 Fernet token（旧库允许混存明文，需区分处理）。"""
    return normalize_ciphertext(value).startswith(FERNET_TOKEN_PREFIX)


@dataclass(slots=True)
class ImportReport:
    """导入结果（CLI 直接展示，测试直接断言）。"""

    accounts_total: int = 0
    accounts_imported: int = 0
    accounts_skipped: int = 0
    accounts_undecryptable: int = 0
    answers_total: int = 0
    answers_imported: int = 0
    answers_skipped: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def imported_anything(self) -> bool:
        """是否有内容被导入。"""
        return self.accounts_imported > 0 or self.answers_imported > 0


def read_rows(legacy_path: Path, sql: str) -> list[sqlite3.Row]:
    """只读查询旧库；表不存在或库损坏都当作"没有这一步的数据"。

    :param legacy_path: 旧库路径
    :param sql: 查询语句
    :returns: 行列表（失败时为空列表并记警告）
    """
    if not legacy_path.exists():
        return []
    connection = sqlite3.connect(f"file:{legacy_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return list(connection.execute(sql))
    except sqlite3.Error as exc:
        log.warning("读取旧库表失败：%s（%s）", sql.strip().split()[3:4], exc)
        return []
    finally:
        connection.close()


def can_decrypt(ciphertext: str, secret_key: str) -> bool:
    """能否用当前密钥解开这段密文（自动剥掉 legacy 前缀）。

    :param ciphertext: 旧库里的密文（可带 ``enc::`` 前缀）
    :param secret_key: 当前 ``SECRET_KEY``（空则无从校验，放行）
    :returns: 可解密（或无需校验）为真
    """
    token = normalize_ciphertext(ciphertext)
    if not token or not secret_key:
        return True
    try:
        from cryptography.fernet import Fernet, InvalidToken
    except ImportError:  # pragma: no cover - 依赖一定在
        return True
    try:
        Fernet(secret_key.encode()).decrypt(token.encode())
    except (InvalidToken, ValueError, TypeError):
        return False
    return True


def extract_answer(answer_json: str) -> str:
    """从旧库的 ``answer_json`` 里取答案文本。

    旧格式是 JSON（可能带选项与解释），新库只存答案本身；取不到就退回原文。

    :param answer_json: 旧字段内容
    :returns: 答案文本（空串表示这条没有可用答案）
    """
    text = (answer_json or "").strip()
    if not text:
        return ""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(data, dict):
        for key in ("answer", "value", "text"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
    if isinstance(data, str):
        return data.strip()
    return text


async def import_legacy_data(
    legacy_path: Path,
    session: AsyncSession,
    *,
    secret_key: str = "",
    dry_run: bool = False,
) -> ImportReport:
    """执行导入（调用方负责事务：通常放在 ``uow.begin()`` 里）。

    :param legacy_path: 旧库路径
    :param session: 新库会话
    :param secret_key: 当前密钥（用于校验旧密文能否解开）
    :param dry_run: 只统计不写库
    :returns: :class:`ImportReport`
    """
    report = ImportReport()

    if not legacy_path.exists():
        report.warnings.append(f"旧库不存在：{legacy_path}")
        return report

    await _import_accounts(legacy_path, session, secret_key, dry_run, report)
    await _import_answers(legacy_path, session, dry_run, report)
    return report


async def _import_accounts(
    legacy_path: Path,
    session: AsyncSession,
    secret_key: str,
    dry_run: bool,
    report: ImportReport,
) -> None:
    """导入账号（属主留空、密文校验通过才收）。"""
    rows = read_rows(
        legacy_path,
        "SELECT username, password_enc, cookie_enc, status, enabled FROM accounts",
    )
    report.accounts_total = len(rows)
    if not rows:
        return

    existing = {str(name) for name in (await session.scalars(select(Account.username))).all()}

    for row in rows:
        username = str(row["username"] or "").strip()
        if not username or username in existing:
            report.accounts_skipped += 1
            continue

        raw_password = str(row["password_enc"] or "")
        password_enc = normalize_ciphertext(raw_password)
        if password_enc and looks_like_fernet_token(password_enc):
            if not can_decrypt(password_enc, secret_key):
                report.accounts_undecryptable += 1
                report.warnings.append(
                    f"{username}：密码密文无法用当前 SECRET_KEY 解密，已跳过（换了密钥？）"
                )
                continue
        elif password_enc:
            # 旧库允许混存明文（legacy 的"明文兼容"设计）：不静默丢弃，但要说清楚
            report.warnings.append(
                f"{username}：密码是明文存储，已原样导入 —— 建议登录后重新保存一次以加密"
            )

        if not dry_run:
            session.add(
                Account(
                    username=username,
                    password_enc=password_enc,
                    cookie_enc=normalize_ciphertext(str(row["cookie_enc"] or "")),
                    enabled=bool(row["enabled"]),
                    status=str(row["status"] or "pending"),
                    # 属主留空：旧库没有平台用户概念
                    owner_user_id=None,
                )
            )
        existing.add(username)
        report.accounts_imported += 1


async def _import_answers(
    legacy_path: Path, session: AsyncSession, dry_run: bool, report: ImportReport
) -> None:
    """导入题库缓存（按题干指纹去重）。"""
    rows = read_rows(
        legacy_path,
        "SELECT question_hash, question_text, answer_json, source, confidence, hits "
        "FROM answer_cache",
    )
    report.answers_total = len(rows)
    if not rows:
        return

    existing = {
        str(value) for value in (await session.scalars(select(AnswerCache.question_hash))).all()
    }

    for row in rows:
        question_hash = str(row["question_hash"] or "").strip()
        answer = extract_answer(str(row["answer_json"] or ""))
        if not question_hash or not answer or question_hash in existing:
            report.answers_skipped += 1
            continue
        if not dry_run:
            session.add(
                AnswerCache(
                    question_hash=question_hash,
                    question=str(row["question_text"] or ""),
                    answer=answer,
                    source=str(row["source"] or "model"),
                    confidence=float(row["confidence"] or 0.0),
                    hits=int(row["hits"] or 0),
                )
            )
        existing.add(question_hash)
        report.answers_imported += 1
