"""进程级依赖装配（容器）。

三处复用同一个容器（``docs/REFACTOR_PLAN.md`` §5.2）：``cx`` 命令行、FastAPI 的
lifespan（P4）、以及测试。

为什么用**显式 dataclass** 而不是服务定位器框架：依赖关系一眼可见、构造顺序明确、
测试里替换任意组件只是换个字段值 —— 对当前规模（十来个组件）这是最省事也最不容易
出错的做法。它同时是唯一允许"横跨各层"的地方（装配点），分层契约在此豁免。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncEngine

from chaoxing.ai import DuckDuckGoSearch, LLMProvider, build_provider
from chaoxing.client import ChaoxingTransport, SessionRegistry
from chaoxing.config import Settings, get_settings
from chaoxing.core.observers import ActivityObserver, DomainLogObserver, TaskEventRecorder
from chaoxing.core.scheduler import AccountScheduler
from chaoxing.core.services import (
    PROMPT_KEY_SYSTEM,
    AccountService,
    AnswerEngine,
    CourseService,
    JobService,
    PlanService,
    PromptService,
    StatsService,
    StudyService,
    UserService,
)
from chaoxing.core.services.audit import AuditService
from chaoxing.core.services.groups import GroupService
from chaoxing.core.services.preferences import PreferenceService
from chaoxing.core.services.quiz_attempts import QuizAttemptService
from chaoxing.obs.account_log import AccountLogWriter
from chaoxing.obs.events import EventBus
from chaoxing.storage.engine import create_engine, create_session_factory
from chaoxing.storage.session_store import DatabaseSessionStore
from chaoxing.storage.uow import UnitOfWork
from chaoxing.utils.crypto import SecretBox
from chaoxing.utils.security import NonceStore

log = logging.getLogger(__name__)


async def _answer_provider(prompts: PromptService, settings: Settings) -> LLMProvider | None:
    """作答前按**当前**提示词构造 provider（提示词热改不需要重启）。

    :param prompts: 提示词服务（库里的文本优先）
    :param settings: 全局配置
    :returns: provider；没配密钥时为 ``None``（引擎退化成"只读题库缓存"）
    """
    # 显式标注：`chaoxing.ai` 的懒导出让静态类型退化为 Any，这里把它钉回协议
    provider: LLMProvider | None = build_provider(
        settings, prompt=await prompts.get(PROMPT_KEY_SYSTEM)
    )
    return provider


@dataclass(slots=True)
class Container:
    """进程内组件的装配结果。"""

    settings: Settings
    engine: AsyncEngine
    uow: UnitOfWork
    box: SecretBox
    bus: EventBus
    account_logs: AccountLogWriter
    transport: ChaoxingTransport
    registry: SessionRegistry
    accounts: AccountService
    courses: CourseService
    plans: PlanService
    prompts: PromptService
    jobs: JobService
    study: StudyService
    stats: StatsService
    users: UserService
    answers: AnswerEngine
    preferences: PreferenceService
    audit: AuditService
    groups: GroupService
    quiz_attempts: QuizAttemptService
    nonces: NonceStore
    scheduler: AccountScheduler
    #: 事件订阅句柄（退出时统一取消）
    _subscriptions: list[object] = field(default_factory=list, repr=False)

    @classmethod
    async def create(cls, settings: Settings | None = None) -> Container:
        """按依赖顺序装配全部组件，并恢复已保存的会话。

        装配顺序：配置 → 密钥 → 引擎/事务 → 事件总线 → 传输层 → 会话注册表 → 服务。

        :param settings: 配置（默认取进程单例）
        :returns: :class:`Container`
        """
        resolved = settings or get_settings()

        engine = create_engine(resolved.db_path)
        uow = UnitOfWork(create_session_factory(engine))
        box = SecretBox(resolved.secret_key)
        bus = EventBus(default_maxsize=resolved.obs.sse_buffer)
        transport = ChaoxingTransport(resolved.chaoxing)
        account_logs = AccountLogWriter(resolved.log_dir / "accounts")
        registry = SessionRegistry(store=DatabaseSessionStore(uow, box))
        prompts = PromptService(uow=uow)
        # 答题引擎必须先于 StudyService 建好**并传进去**：不传的话 StudyService 会
        # 自己 fallback 成 `AnswerEngine(uow=uow)`（没有 provider），
        # 表现为"题目都取到了、却一题都答不出来"且请求瞬间返回（真机事故 2026-10-12）。
        answers = AnswerEngine(
            uow=uow,
            provider=build_provider(resolved),
            provider_factory=lambda: _answer_provider(prompts, resolved),
            search=DuckDuckGoSearch(),
            settings=resolved,
        )
        # 课程服务只有一个实例（订阅/状态只此一份）：先于 StudyService 建好并注入，
        # 让测验执行前能就地刷新票据（扫描时存的 enc 几分钟就轮换）。
        courses = CourseService(uow=uow, transport=transport, registry=registry, bus=bus)
        # 刷课服务只建一个实例：CLI 与调度器共用同一份（否则事件订阅会出现两份）
        study = StudyService(
            uow=uow,
            transport=transport,
            registry=registry,
            bus=bus,
            settings=resolved,
            answers=answers,
            courses=courses,
            account_log=account_logs,
        )
        stats = StatsService(uow=uow)

        container = cls(
            settings=resolved,
            engine=engine,
            uow=uow,
            box=box,
            bus=bus,
            account_logs=account_logs,
            transport=transport,
            registry=registry,
            accounts=AccountService(
                uow=uow, transport=transport, registry=registry, box=box, bus=bus
            ),
            courses=courses,
            plans=PlanService(uow=uow),
            prompts=prompts,
            jobs=JobService(uow=uow),
            study=study,
            stats=stats,
            users=UserService(uow=uow),
            answers=answers,
            preferences=PreferenceService(uow=uow, base=resolved),
            audit=AuditService(uow=uow),
            groups=GroupService(uow=uow),
            quiz_attempts=QuizAttemptService(uow=uow),
            nonces=NonceStore(),
            scheduler=AccountScheduler(
                study=study, max_concurrency=resolved.chaoxing.max_account_concurrency
            ),
        )
        container._subscriptions = [
            bus.subscribe(TaskEventRecorder(uow).handle),
            bus.subscribe(DomainLogObserver().handle),
            bus.subscribe(ActivityObserver(stats).handle),
        ]

        # 权限目录自愈：存量库缺节点 = 路由要求一个谁都没有的权限 = 整页 403
        await container.users.sync_catalog()

        restored = await registry.restore()
        if restored:
            log.info("已从数据库恢复 %d 个账号会话", restored)
        return container

    async def aclose(self) -> None:
        """按相反顺序收尾：总线 → 传输层 → 数据库引擎。"""
        await self.bus.close()
        await self.transport.aclose()
        await self.engine.dispose()
