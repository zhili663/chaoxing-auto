"""口令与会话安全（``docs/REFACTOR_PLAN.md`` §5.8 路线 A）。

放在 ``utils`` 而不是 ``api``：``core`` 的服务层要用它（建用户、校验口令），
而 ``core`` 不能依赖 ``api`` —— 分层契约会直接报错。这里只依赖标准库与 argon2，
是纯粹的算法与状态，没有任何框架痕迹。

口令链路
--------
::

    注册  C = sha256(password)          ← 前端 WebCrypto，明文不出浏览器
          后端存 Argon2id(C)            ← 加盐慢哈希，绝不明文、也绝不存 C
    登录  直接传 C（TLS 内），后端 Argon2id.verify(C, stored)

**为什么登录不掺 nonce（这是一个数学事实，不是偷懒）**：服务端手上只有
``Argon2id(C)``，若客户端发来 ``proof = sha256(C + ":" + nonce)``，
服务端无法从 proof 反推 C，也就无法用存储哈希校验 —— 除非它把 C 也存下来，
而那等于把口令等效物明文落库。

因此安全边界写清楚：登录态依赖 **TLS + HttpOnly Cookie + 严格限速**，
``nonce`` 只用于**注册**与**改密**这类低频高危动作（防重放）。
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

#: 前端传来的 ``sha256(password)``：64 位小写十六进制
CLIENT_SECRET_PATTERN = re.compile(r"^[0-9a-f]{64}$")

#: Argon2id 参数（默认即合理：内存 64MB、时间代价 3、并行 4）
_hasher = PasswordHasher()

#: 一次性 nonce 的默认存活时间（秒）
NONCE_TTL = 60


def looks_like_client_secret(value: str) -> bool:
    """判断是否是前端哈希后的口令。

    这条校验拦的是"前端忘了哈希"—— 若直接放明文进来，库里存的就成了明文的慢哈希，
    与注册时写入的口径不一致，用户之后怎么也登不上。
    """
    return bool(CLIENT_SECRET_PATTERN.match(value or ""))


def hash_secret(secret: str) -> str:
    """把前端哈希后的口令再哈希（Argon2id，加盐）。

    :param secret: ``sha256(password)``
    :raises ValueError: 传入的不是前端哈希值
    :returns: Argon2id 哈希串
    """
    if not looks_like_client_secret(secret):
        raise ValueError("口令必须是前端 sha256 后的 64 位十六进制字符串")
    return _hasher.hash(secret)


def verify_secret(secret: str, stored: str) -> bool:
    """校验口令。

    :param secret: ``sha256(password)``
    :param stored: 库里的 Argon2id 哈希
    :returns: 是否匹配（哈希损坏或不匹配都返回 False）
    """
    if not secret or not stored:
        return False
    try:
        return _hasher.verify(stored, secret)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored: str) -> bool:
    """库里的哈希是否该用当前参数重算（参数升级后自动迁移）。"""
    try:
        return _hasher.check_needs_rehash(stored)
    except (InvalidHashError, VerificationError):
        return False


def new_session_token() -> str:
    """生成不透明会话 token（明文只出现在 Cookie 里）。"""
    return secrets.token_urlsafe(32)


def token_fingerprint(token: str) -> str:
    """token 的指纹（**库里只存它**，库被读走也无法直接冒用登录态）。"""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def client_ip(headers: dict[str, str], fallback: str = "") -> str:
    """从常见转发头里取客户端 IP（反向代理后面才准）。"""
    forwarded = headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return headers.get("x-real-ip", "") or fallback


@dataclass(slots=True)
class _Nonce:
    """一条待用的 nonce。"""

    value: str
    expires_at: float
    scene: str


class NonceStore:
    """一次性 nonce（内存版，单进程部署是既定约束）。

    只服务**注册**与**改密**：签发后限时使用、用完即焚，重复使用直接失败。
    """

    def __init__(self, *, ttl: int = NONCE_TTL) -> None:
        self._ttl = ttl
        self._items: dict[str, _Nonce] = {}

    def issue(self, scene: str) -> str:
        """签发一个 nonce。

        :param scene: 场景标记（register / passwd）
        :returns: nonce 值
        """
        self._purge()
        value = secrets.token_urlsafe(24)
        self._items[value] = _Nonce(value=value, expires_at=time.time() + self._ttl, scene=scene)
        return value

    def consume(self, value: str, scene: str) -> bool:
        """消费一个 nonce（成功即失效）。

        :param value: 客户端回传的 nonce
        :param scene: 期望的场景
        :returns: 是否有效
        """
        self._purge()
        item = self._items.pop(value, None)
        if item is None:
            return False
        return item.scene == scene and item.expires_at >= time.time()

    def _purge(self) -> None:
        """清掉过期项（避免长期运行下无限增长）。"""
        now = time.time()
        expired = [key for key, item in self._items.items() if item.expires_at < now]
        for key in expired:
            self._items.pop(key, None)
