"""凭据加解密：与 legacy 密文格式对齐（无前缀的标准 Fernet token）。

来源：迁移时踩过的坑 —— 旧库密文带 ``enc::`` 前缀，新库存标准 token，
两侧若不一致，"导入的账号能不能用"就变成了运气问题。
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from chaoxing.utils.crypto import SecretBox, SecretError


def test_round_trip_encrypt_decrypt() -> None:
    """加解密往返。"""
    box = SecretBox(SecretBox.generate_key())

    token = box.encrypt("my-password")

    assert token != "my-password"
    assert box.decrypt(token) == "my-password"


def test_token_has_no_custom_prefix() -> None:
    """密文是标准 Fernet token（以 ``gAAAAA`` 开头），不带 legacy 的 ``enc::``。"""
    box = SecretBox(SecretBox.generate_key())

    token = box.encrypt("x")

    assert token.startswith("gAAAAA")
    assert not token.startswith("enc::")


def test_empty_values_pass_through() -> None:
    """ ""空"就是空：不加密成一段密文，也便于判断"这条有没有值"。"""
    box = SecretBox(SecretBox.generate_key())

    assert box.encrypt("") == ""
    assert box.decrypt("") == ""


def test_wrong_key_reports_secret_error() -> None:
    """换了密钥必须报明确错误（而不是丢一个 InvalidToken 上去）。"""
    token = SecretBox(Fernet.generate_key().decode()).encrypt("secret")
    other = SecretBox(Fernet.generate_key().decode())

    assert other.can_decrypt(token) is False
    with pytest.raises(SecretError, match="SECRET_KEY"):
        other.decrypt(token)


def test_invalid_key_is_rejected_early() -> None:
    """密钥本身非法时构造函数就拦下（而不是等到第一次加密）。"""
    with pytest.raises(SecretError, match="Fernet"):
        SecretBox("not-a-valid-key")

    with pytest.raises(SecretError, match="SECRET_KEY"):
        SecretBox("")


def test_json_round_trip() -> None:
    """会话快照用字典存取。"""
    box = SecretBox(SecretBox.generate_key())
    payload = {"cookies": {"_uid": "42"}, "tokens": {"enc": "abc"}}

    restored = box.decrypt_json(box.encrypt_json(payload))

    assert restored == payload


def test_broken_json_returns_empty_dict() -> None:
    """库里存了坏数据时返回空字典，不拖垮启动流程。"""
    box = SecretBox(SecretBox.generate_key())
    broken = box.encrypt("{ not json")

    assert box.decrypt_json(broken) == {}


def test_generated_key_is_forty_four_chars() -> None:
    """生成的密钥长度固定（44 字符的 urlsafe base64）—— 部署文档里要写死这个约定。"""
    assert len(SecretBox.generate_key()) == 44
