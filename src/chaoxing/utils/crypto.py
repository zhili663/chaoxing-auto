"""凭据加解密（Fernet）。

新库的密文是**标准 Fernet token，不带任何前缀** —— 与导入时剥离的 legacy ``enc::``
前缀保持一致的约定：格式由加密库决定，不再自造标记位（"这条是否已加密"由字段是否为空判断）。

密钥来源：``SECRET_KEY``（44 字符的 urlsafe base64）。**换密钥等于旧密文全部作废**，
因此 `cx doctor` 会在密钥缺失时提醒，而不是默默生成一个新的。
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class SecretError(Exception):
    """密钥或密文有问题。

    刻意**不继承** client 层的 ``ChaoxingError``：这属于配置/数据问题而非协议错误，
    而且 ``utils`` 是最底层，一旦依赖 client 就会把 storage 也拖下水
    （import-linter 的 "storage must not depend on domain" 契约会直接报错）。
    """


class SecretBox:
    """账号凭据的对称加解密。

    :param key: Fernet key（``SECRET_KEY``）
    :raises SecretError: 密钥不是合法的 Fernet key
    """

    def __init__(self, key: str) -> None:
        if not key:
            raise SecretError(
                "SECRET_KEY 为空：无法加解密账号凭据（可执行 `cx doctor --fix` 生成）"
            )
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise SecretError(
                "SECRET_KEY 不是合法的 Fernet key（应为 44 字符的 urlsafe base64）"
            ) from exc

    @staticmethod
    def generate_key() -> str:
        """生成一个新的 Fernet key（供 ``cx doctor --fix`` 使用）。"""
        return Fernet.generate_key().decode("ascii")

    def encrypt(self, plaintext: str) -> str:
        """加密（空串原样返回，避免把"未设置"变成一段密文）。

        :param plaintext: 明文
        :returns: Fernet token
        """
        if not plaintext:
            return ""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        """解密。

        :param token: Fernet token（空串原样返回）
        :raises SecretError: 密文损坏或密钥不匹配
        :returns: 明文
        """
        if not token:
            return ""
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, TypeError) as exc:
            raise SecretError("解密失败：SECRET_KEY 可能已变更") from exc

    def can_decrypt(self, token: str) -> bool:
        """能否解开（用于导入前的校验，不抛异常）。"""
        try:
            self.decrypt(token)
        except SecretError:
            return False
        return True

    def encrypt_json(self, payload: dict[str, object]) -> str:
        """把字典加密成一段密文（会话快照用）。"""
        import json

        return self.encrypt(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    def decrypt_json(self, token: str) -> dict[str, object]:
        """解密回字典（坏数据返回空字典，不拖垮启动流程）。"""
        import json

        raw = self.decrypt(token)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}
