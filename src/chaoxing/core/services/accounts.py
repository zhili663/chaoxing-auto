"""账号服务：登录、登出、登录态刷新、凭据加密落库。

编排职责（既不碰 HTTP 细节，也不写 SQL）：

1. 从库里取账号 → 解密密码 → 交给 client 层的 ``LoginApi``；
2. 登录成功后把会话快照（cookie / 令牌）**加密落库**，并同步进 ``SessionRegistry``；
3. 全程发领域事件（登录成功 / 需要人工），供 SSE 与审计消费 —— 本模块不知道谁在听。

依赖方向：``core → client / storage / obs``，符合分层契约。
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from chaoxing.client import (
    ChaoxingTransport,
    LoginApi,
    LoginResult,
    SessionRegistry,
    SessionState,
)
from chaoxing.obs.events import AccountLoggedIn, EventBus, HumanRequired, SessionExpired
from chaoxing.storage.base import utcnow
from chaoxing.storage.models import Account
from chaoxing.storage.uow import UnitOfWork
from chaoxing.utils.crypto import SecretBox

log = logging.getLogger(__name__)

#: 账号状态（``Account.status`` 的取值）——「启用/停用」是另一个布尔字段 ``enabled``
ACCOUNT_STATUSES: tuple[str, ...] = ("pending", "logged_in", "expired")


class AccountNotFound(Exception):
    """账号不存在（与"登录失败"分开：前者是调用方搞错了 ID）。"""


class AccountExists(Exception):
    """同名账号已存在。"""


class AccountService:
    """账号生命周期编排。

    :param uow: 工作单元（事务边界）
    :param transport: 学习通传输层
    :param registry: 会话注册表
    :param box: 凭据加解密
    :param bus: 事件总线
    """

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        transport: ChaoxingTransport,
        registry: SessionRegistry,
        box: SecretBox,
        bus: EventBus,
    ) -> None:
        self._uow = uow
        self._transport = transport
        self._registry = registry
        self._box = box
        self._bus = bus
        self._login_api = LoginApi(transport)

    # ------------------------------------------------------------------ #
    # 读
    # ------------------------------------------------------------------ #
    async def get(self, account_id: int) -> Account:
        """按 ID 取账号。

        :raises AccountNotFound: ID 不存在
        """
        async with self._uow.read() as session:
            account = await session.get(Account, account_id)
            if account is None:
                raise AccountNotFound(f"账号 {account_id} 不存在")
            return account

    async def list_accounts(
        self, *, owner_user_id: int | None = None, group_id: int | None = None
    ) -> list[Account]:
        """全部账号（按刷课顺序）。

        :param owner_user_id: 只列某人的账号（``None`` = 不限，管理员视角）。
            数据可见性由调用方按权限决定，服务层只做过滤不做判断 ——
            这样 CLI 与 API 能用同一套逻辑，不会出现两套口径。
        :param group_id: 只列某个虚拟账户组里的账号（``None`` = 不限）
        """
        from sqlalchemy import select

        from chaoxing.storage.models import account_group_members

        async with self._uow.read() as session:
            statement = select(Account).order_by(Account.priority, Account.id)
            if owner_user_id is not None:
                statement = statement.where(Account.owner_user_id == owner_user_id)
            if group_id is not None:
                statement = statement.where(
                    Account.id.in_(
                        select(account_group_members.c.account_id).where(
                            account_group_members.c.group_id == group_id
                        )
                    )
                )
            return list((await session.scalars(statement)).all())

    # ------------------------------------------------------------------ #
    # 新增 / 更新
    # ------------------------------------------------------------------ #
    async def create_account(
        self,
        *,
        username: str,
        password: str = "",
        owner_user_id: int | None = None,
        priority: int = 100,
        note: str = "",
    ) -> Account:
        """新增超星账号（密码加密落库）。

        :param username: 超星账号（手机号 / 学号）
        :param password: 明文密码（空串表示"先录入，稍后再登录"）
        :param owner_user_id: 属主（``None`` = 共享账号）
        :raises AccountExists: 同名账号已存在
        :returns: 新建的账号
        """
        from sqlalchemy import select

        async with self._uow.begin() as session:
            existing = (
                await session.scalars(select(Account).where(Account.username == username))
            ).first()
            if existing is not None:
                raise AccountExists(f"账号 {username} 已存在")

            account = Account(
                username=username,
                password_enc=self._box.encrypt(password),
                owner_user_id=owner_user_id,
                priority=priority,
                note=note,
            )
            session.add(account)
            await session.flush()
            log.info("已新增账号 %s（属主 %s）", username, owner_user_id)
            return account

    async def delete_account(self, account_id: int) -> bool:
        """删除账号（课程与任务点按外键级联清理，本地会话同时失效）。

        :param account_id: 账号 ID
        :returns: 是否存在并被删除
        """
        async with self._uow.begin() as session:
            account = await session.get(Account, account_id)
            if account is None:
                return False
            await session.delete(account)
            await session.flush()
        self._registry.remove(account_id)
        return True

    async def set_enabled(self, account_id: int, *, enabled: bool) -> bool:
        """启用 / 停用账号（停用后不会参与刷课）。

        :param account_id: 账号 ID
        :param enabled: 是否启用
        :returns: 是否存在并被更新
        """
        async with self._uow.begin() as session:
            account = await session.get(Account, account_id)
            if account is None:
                return False
            account.enabled = enabled
            await session.flush()
        if not enabled:
            # 停用即清掉本地会话：留着它下次会被"复活"成可用状态，与停用语义相悖
            self._registry.remove(account_id)
        log.info("账号 %s %s", account_id, "已启用" if enabled else "已停用")
        return True

    # ------------------------------------------------------------------ #
    # 登录 / 登出 / 刷新
    # ------------------------------------------------------------------ #
    async def login(
        self,
        account_id: int,
        *,
        password: str | None = None,
        captcha: str = "",
        encoding: str = "plain",
    ) -> LoginResult:
        """登录指定账号。

        未提供 ``password`` 时用库里已存的密文解密；**提供了新密码则加密后覆盖落库**
        （这也是一条"明文密码转加密"的修复路径）。

        :param account_id: 账号 ID
        :param password: 明文密码（可选）
        :param captcha: 图形验证码（服务端索要时才有）
        :param encoding: 密码编码方式（plain / base64 / md5）
        :returns: :class:`~chaoxing.client.LoginResult`
        """
        account = await self.get(account_id)
        secret = password if password is not None else self._box.decrypt(account.password_enc)
        if not secret:
            result = LoginResult(
                ok=False, message="没有可用密码，请先用 --password 提供", reason="credential"
            )
            return result

        async with self._registry.acquire(account_id) as session:
            session.account_id = account_id
            result = await self._login_api.login(
                session, account.username, secret, captcha=captcha, encoding=encoding
            )
            if result.ok:
                await self._store_session(account_id, session, new_password=password)
                self._bus.publish(AccountLoggedIn(account_id=account_id, username=account.username))
                log.info("账号 %s 登录成功", account_id)
            elif result.need_human:
                self._bus.publish(
                    HumanRequired(
                        account_id=account_id, reason=result.reason, detail=result.message
                    )
                )
            else:
                await self._mark_status(account_id, "expired", error=result.message)
            return result

    async def refresh(self, account_id: int) -> bool:
        """用已存的登录态校验一次（不发密码，不触发验证码）。

        :param account_id: 账号 ID
        :returns: 登录态是否仍然有效
        """
        state = await self._load_session(account_id)
        if state is None:
            return False
        ok = await self._login_api.verify_session(state)
        await self._mark_status(account_id, "logged_in" if ok else "expired")
        if not ok:
            self._bus.publish(SessionExpired(account_id=account_id, reason="校验失败"))
        return ok

    async def logout(self, account_id: int) -> None:
        """清除本地登录态（不动学习通侧会话）。"""
        self._registry.remove(account_id)
        await self._mark_status(account_id, "pending")
        log.info("账号 %s 已登出（本地态已清）", account_id)

    async def ensure_session(self, account_id: int) -> SessionState:
        """取（必要时从库里恢复）会话对象。

        :param account_id: 账号 ID
        :raises AccountNotFound: 账号不存在
        :returns: 会话状态
        """
        if not self._registry.has(account_id):
            restored = await self._load_session(account_id)
            if restored is not None:
                self._registry.put(restored)
        return self._registry.get(account_id)

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    async def _load_session(self, account_id: int) -> SessionState | None:
        """从库里恢复会话快照（cookie 是加密存的）。"""
        account = await self._safe_get(account_id)
        if account is None or not account.cookie_enc:
            return None
        snapshot = self._box.decrypt_json(account.cookie_enc)
        if not snapshot:
            return None
        snapshot.setdefault("account_id", account_id)
        snapshot.setdefault("username", account.username)
        snapshot.setdefault("status", account.status or "logged_in")
        return SessionState.from_snapshot(snapshot)

    async def _safe_get(self, account_id: int) -> Account | None:
        """取账号；不存在返回 None（供内部路径使用，不抛异常）。"""
        async with self._uow.read() as session:
            return await session.get(Account, account_id)

    async def _store_session(
        self, account_id: int, state: SessionState, *, new_password: str | None = None
    ) -> None:
        """把会话快照与新密码写回库（一个事务内完成）。"""
        async with self._uow.begin() as session:
            await self._apply_session(session, account_id, state, new_password=new_password)

    async def _apply_session(
        self,
        session: AsyncSession,
        account_id: int,
        state: SessionState,
        *,
        new_password: str | None = None,
    ) -> None:
        """事务内的实际写入逻辑。"""
        account = await session.get(Account, account_id)
        if account is None:
            return
        account.cookie_enc = self._box.encrypt_json(state.snapshot())
        account.status = state.status or "logged_in"
        account.session_state = {"status": state.status}
        account.last_check_at = utcnow()
        if new_password is not None:
            account.password_enc = self._box.encrypt(new_password)
        await session.flush()

    async def _mark_status(self, account_id: int, status: str, *, error: str = "") -> None:
        """更新账号状态（登录态失效、人工介入等都走这里）。"""
        async with self._uow.begin() as session:
            account = await session.get(Account, account_id)
            if account is None:
                return
            account.status = status
            account.last_check_at = utcnow()
            if error:
                account.note = error[:255]
            await session.flush()
