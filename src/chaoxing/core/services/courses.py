"""课程服务：扫描入库、列表、勾选。

职责边界："刷什么"由用户在网页/CLI 上决定（零硬编码），本服务只做两件事 ——
把学习通的课程**拉下来写库**（且绝不覆盖用户的勾选与优先级），以及执行勾选操作。
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlparse

from sqlalchemy import select

from chaoxing.client import (
    ChaoxingTransport,
    ChapterApi,
    CourseApi,
    SessionRegistry,
    SessionState,
)
from chaoxing.client.api.course import extract_stuenc
from chaoxing.client.endpoints import REGISTRY
from chaoxing.client.exceptions import ErrorPageDetected, SessionExpired
from chaoxing.client.model.card import JobCard
from chaoxing.client.parser.chapter import parse_chapter_progress, parse_chapter_tree
from chaoxing.obs.events import CourseScanned, EventBus
from chaoxing.storage.models import Course, TaskPoint
from chaoxing.storage.repositories import CourseRepo, TaskPointRepo
from chaoxing.storage.uow import UnitOfWork

log = logging.getLogger(__name__)


class NotLoggedIn(Exception):
    """账号没有可用的登录态（需要先 `cx auth login`）。"""


class CourseNotFound(Exception):
    """课程不在本地缓存里（需要先扫描）。"""


class ScanInProgress(Exception):
    """同一账号已有一个扫描在跑。

    并发扫描会触发学习通反爬风控（真机事故 2026-10-14），所以拿不到锁时**直接抛**，
    绝不让请求排队等待 —— 排队会让用户以为卡死。
    """


def _split_key(course_key: str) -> tuple[str, str]:
    """把 ``courseId:clazzId`` 拆成两段（缺失时返回空串，交给上层报错）。"""
    course_id, _, clazz_id = course_key.partition(":")
    return course_id.strip(), clazz_id.strip()


@dataclass(slots=True)
class ScanReport:
    """一次课程扫描的结果。"""

    account_id: int = 0
    total: int = 0
    created: int = 0
    updated: int = 0
    courses: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PointScanReport:
    """一次任务点扫描的结果。"""

    account_id: int = 0
    course_key: str = ""
    chapters: int = 0
    created: int = 0
    updated: int = 0
    kinds: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    #: 顺手做的完成状态同步结果（扫描时自动带上）
    progress: ProgressSyncReport | None = None

    @property
    def total(self) -> int:
        """本次看到的任务点总数。"""
        return self.created + self.updated


@dataclass(slots=True)
class ProgressSyncReport:
    """一次"完成状态同步"的结果。

    只统计**被纠正**的量（本地以为没做、学习通侧其实做完的），
    因为这个数字直接说明"本地状态与现实差多远"。
    """

    account_id: int = 0
    course_key: str = ""
    chapters: int = 0
    remote_done: int = 0
    marked: int = 0
    #: 本次目录页明确标为已完成的章节 ID（供单任务点复核，不能拿旧 done 行倒推）
    done_chapters: set[str] = field(default_factory=set)
    kinds: dict[str, int] = field(default_factory=dict)


class CourseService:
    """课程列表的同步与选择。

    :param uow: 工作单元
    :param transport: 学习通传输层
    :param registry: 会话注册表
    :param bus: 事件总线
    """

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        transport: ChaoxingTransport,
        registry: SessionRegistry,
        bus: EventBus,
    ) -> None:
        self._uow = uow
        self._transport = transport
        self._registry = registry
        self._bus = bus
        self._api = CourseApi(transport)
        self._chapter_api = ChapterApi(transport)
        # 账号级扫描互斥锁（进程内）：服务是单进程（workers=1），进程内锁即覆盖全部请求。
        self._scan_locks: dict[int, asyncio.Lock] = {}

    # ------------------------------------------------------------------ #
    # 扫描互斥
    # ------------------------------------------------------------------ #
    def _scan_lock(self, account_id: int) -> asyncio.Lock:
        """取账号级扫描锁（同账号的"扫描课程"与"扫描任务点"共用一把）。

        :param account_id: 账号 ID
        :returns: 该账号的锁（不存在则创建）
        """
        lock = self._scan_locks.get(account_id)
        if lock is None:
            lock = self._scan_locks[account_id] = asyncio.Lock()
        return lock

    @asynccontextmanager
    async def _scan_guard(self, account_id: int) -> AsyncIterator[None]:
        """账号级扫描互斥：**拿不到锁直接抛**（不排队），异常路径也一定释放。

        :param account_id: 账号 ID
        :raises ScanInProgress: 该账号已有扫描在跑
        """
        lock = self._scan_lock(account_id)
        if lock.locked():
            raise ScanInProgress(f"账号 {account_id} 正在扫描，请等这一次扫完")
        async with lock:
            yield

    # ------------------------------------------------------------------ #
    # 扫描
    # ------------------------------------------------------------------ #
    async def scan(self, account_id: int) -> ScanReport:
        """拉取"我的课程"并写库（幂等：重复扫描只更新标题）。

        :param account_id: 账号 ID
        :raises NotLoggedIn: 该账号没有可用登录态
        :raises ScanInProgress: 该账号已有扫描在跑
        :returns: :class:`ScanReport`
        """
        async with self._scan_guard(account_id):
            return await self._scan_locked(account_id)

    async def _scan_locked(self, account_id: int) -> ScanReport:
        session = self._session_for(account_id)
        courses = await self._api.list_courses(session)

        report = ScanReport(account_id=account_id, total=len(courses))
        async with self._uow.begin() as db:
            repo = CourseRepo(db)
            for course in courses:
                existed = await repo.get_by_key(account_id, course.key) is not None
                await repo.upsert_from_scan(
                    account_id,
                    course_key=course.key,
                    title=course.title,
                    teacher=course.teacher,
                    cover=course.cover,
                )
                if existed:
                    report.updated += 1
                else:
                    report.created += 1
                report.courses.append(course.key)

        log.info(
            "账号 %s 扫描完成：%d 门（新增 %d / 更新 %d）",
            account_id,
            report.total,
            report.created,
            report.updated,
        )
        return report

    # ------------------------------------------------------------------ #
    # 任务点扫描
    # ------------------------------------------------------------------ #
    async def scan_points(self, account_id: int, course_key: str) -> PointScanReport:
        """扫描一门课的章节与任务点并入库。

        流程（每一步都不可省，都是实测踩出来的）：

        1. **课程入口**：拿 ``cpi`` 与 ``stuenc`` —— 少了它们，章节页直接给
           ``[3004] 长时间没有操作或没有权限``；
        2. 章节内容页 → :func:`~chaoxing.client.parser.chapter.parse_chapter_tree` 抽章节；
        3. 逐章取卡片 → 解析任务点（``_jobid`` 已由解析层从 ``property`` 里摊平）；
        4. 入库时把卡片透传字段**原样存进 payload** —— 心跳与提交全靠它。

        :param account_id: 账号 ID
        :param course_key: ``courseId:clazzId``
        :raises NotLoggedIn: 没有可用登录态
        :raises CourseNotFound: 课程不在本地缓存里
        :raises ScanInProgress: 该账号已有扫描在跑
        :returns: :class:`PointScanReport`
        """
        async with self._scan_guard(account_id):
            return await self._scan_points_locked(account_id, course_key)

    async def _scan_points_locked(self, account_id: int, course_key: str) -> PointScanReport:
        session = self._session_for(account_id)
        course_id, clazz_id = _split_key(course_key)

        async with self._uow.read() as db:
            stored = await CourseRepo(db).get_by_key(account_id, course_key)
            if stored is None:
                raise CourseNotFound(f"课程 {course_key} 不在本地缓存，先执行 `cx course scan`")
            local_id = stored.id

        entry_url = await self._api.enter_course(session, course_id=course_id, clazz_id=clazz_id)
        entry_query = dict(parse_qsl(urlparse(entry_url).query))
        cpi = entry_query.get("cpi", "")
        stuenc = extract_stuenc(entry_url)

        html = await self._chapter_api.fetch_content_html(
            session,
            course_id=course_id,
            clazz_id=clazz_id,
            cpi=cpi,
            stuenc=stuenc,
        )
        chapters = parse_chapter_tree(html)
        # 换票要用的 ``utEnc`` 是**课程级**的（真机实测 2026-10-12）：取一次、注进每个任务点的
        # payload；取不到也不影响扫描主流程（只是该课程的测验会在换票那一步被弹登录）。
        utenc = await self._fetch_utenc(
            session,
            entry_url=entry_url,
            chapter_id=chapters[0].chapter_id if chapters else "",
            course_id=course_id,
            clazz_id=clazz_id,
            cpi=cpi,
        )
        report = PointScanReport(
            account_id=account_id, course_key=course_key, chapters=len(chapters)
        )
        if not chapters:
            report.warnings.append("没有解析到章节（页面结构可能变了）")
            return report

        for chapter in chapters:
            # ★ 一个章节可能有**多个任务点**（视频、章节测验、文档…），它们共用同一个
            #   卡片接口、只靠 ``num`` 区分。只取默认 num 会漏掉视频之后的所有任务点 ——
            #   实测 2026-10-15：`num=0` 是视频、`num=1` 是「章节测验」、越界的 num 返回
            #   **空附件**（用它当终止哨兵，不必去猜每章有几个任务点）。
            seen_cards: set[str] = set()
            for num in range(10):  # 上限只是兜底，正常靠"空附件"提前收工
                try:
                    card = await self._chapter_api.fetch_cards(
                        session,
                        course_id=course_id,
                        clazz_id=clazz_id,
                        knowledge_id=chapter.chapter_id,
                        cpi=cpi,
                        stuenc=stuenc,
                        num=num,
                    )
                except ErrorPageDetected as exc:
                    # 学习通有章节解锁机制：没解锁的章节会回 [3004]。
                    # 那是**正常状态**，不该让整门课的扫描失败 —— 记一条警告继续。
                    report.warnings.append(
                        f"章节 {chapter.chapter_id} 暂时不可访问（{exc.marker}），已跳过"
                    )
                    break
                if card is None:
                    report.warnings.append(f"章节 {chapter.chapter_id} 的卡片无法解析")
                    break
                if not card.attachments:
                    break  # 该章节的任务点已经全部取完
                fingerprint = "|".join(
                    f"{item.kind}:{item.object_id or item.work_id}" for item in card.attachments
                )
                if fingerprint in seen_cards:
                    break  # 服务端对越界 num 也可能回落给同一张卡
                seen_cards.add(fingerprint)
                await self._store_points(
                    account_id=account_id,
                    local_course_id=local_id,
                    remote_course_id=course_id,
                    chapter=chapter,
                    card=card,
                    fallback_clazz=clazz_id,
                    fallback_cpi=cpi,
                    utenc=utenc,
                    num=num,
                    report=report,
                )

        # 顺手同步完成状态：卡片里没有完成标记，只在目录页上；不同步的话
        # 刷课会去撞那些"本地以为没做、实际早做完"的点（既发无效请求，
        # 也永远等不到心跳 —— 服务端不会为已完成的任务点产生上报）。
        try:
            report.progress = await self.sync_progress(account_id, course_key)
        except Exception as exc:
            report.warnings.append(f"完成状态同步失败（{type(exc).__name__}），本地状态可能偏旧")
            log.warning("课程 %s 完成状态同步失败：%s", course_key, exc)

        self._bus.publish(
            CourseScanned(account_id=account_id, course_key=course_key, point_count=report.total)
        )
        log.info(
            "课程 %s 扫描完成：%d 章 / %d 个任务点（新增 %d）",
            course_key,
            report.chapters,
            report.total,
            report.created,
        )
        return report

    async def sync_progress(self, account_id: int, course_key: str) -> ProgressSyncReport:
        """把学习通侧的章节完成状态同步到本地任务点。

        数据来源是**课程目录页**（``mooc2-ans/mycourse/studentcourse``）——
        卡片响应里**没有**任何完成标记（实测两个章节都搜不到），所以只能从这里读。
        页面上已完成项带 ``catalog_state.icon_yiwanc``，未完成项**根本没有状态节点**。

        **只重标"已完成"**：把本地标为 ``done``，但绝不把 ``done`` 改回未完成 ——
        服务端说"做完了"是硬事实，而"没看到完成标记"可能是页面版本差异，
        拿它去推翻已有结论会丢进度。

        :param account_id: 账号 ID
        :param course_key: ``courseId:clazzId``
        :raises NotLoggedIn: 没有可用登录态
        :returns: :class:`ProgressSyncReport`
        """
        session = self._session_for(account_id)
        course_id, clazz_id = _split_key(course_key)

        async with self._uow.read() as db:
            stored = await CourseRepo(db).get_by_key(account_id, course_key)
            if stored is None:
                raise CourseNotFound(f"课程 {course_key} 不在本地缓存，先执行 `cx course scan`")
            local_id = stored.id

        entry_url = await self._api.enter_course(session, course_id=course_id, clazz_id=clazz_id)
        entry_query = dict(parse_qsl(urlparse(entry_url).query))
        cpi = entry_query.get("cpi", "")
        stuenc = extract_stuenc(entry_url)

        html = await self._chapter_api.fetch_content_html(
            session,
            course_id=course_id,
            clazz_id=clazz_id,
            cpi=cpi,
            stuenc=stuenc,
        )
        progress = parse_chapter_progress(html)
        report = ProgressSyncReport(
            account_id=account_id,
            course_key=course_key,
            chapters=len(progress),
            remote_done=sum(1 for done in progress.values() if done),
        )
        if not progress:
            report.kinds["_warning"] = 0
            log.warning("课程 %s 的目录页没解析出完成状态（页面版本可能变了）", course_key)
            return report

        done_chapters = {key for key, done in progress.items() if done}
        report.done_chapters = done_chapters
        async with self._uow.begin() as db:
            rows = list(
                (await db.scalars(select(TaskPoint).where(TaskPoint.course_id == local_id))).all()
            )
            for row in rows:
                chapter = str((row.payload or {}).get("knowledgeid", ""))
                if chapter in done_chapters and row.status != "done":
                    row.status = "done"
                    row.progress = row.duration
                    report.marked += 1
                    report.kinds[row.kind] = report.kinds.get(row.kind, 0) + 1

        if report.marked:
            log.info("课程 %s 完成状态同步：%d 个任务点被纠正为已完成", course_key, report.marked)
        return report

    async def _fetch_utenc(
        self,
        session: SessionState,
        *,
        entry_url: str,
        chapter_id: str,
        course_id: str,
        clazz_id: str,
        cpi: str,
    ) -> str:
        """取课程页的 ``utEnc``（测验换票参数，真机实测 2026-10-12）。

        两步都不能省：课程落地页里带**章节页的 ``enc``**（形如 ``enc=<32 位 hex>``），
        用它打开任一章节点，才能从内联脚本里读到 ``var utEnc="…"``。
        少了它换票会被弹到登录页（表现为「换票后未落到题目页」）——
        这是浏览器能进题目页、我们进不去的最后一处差异。

        :param chapter_id: 任一章节点 ID（``utEnc`` 是课程级的，取一次即可）
        :returns: ``utEnc``；任一步失败都返回空串（不影响扫描主流程）
        """
        if not chapter_id:
            return ""
        try:
            landing = await self._transport.request(session, entry_url, follow_redirects=True)
            # 落地页里有几个隐藏 input（真机实测 2026-10-12）：
            #   id="enc"（当前，配 openc 才有效）、id="oldenc"（上一次的，实测直接可用）、id="openc"（章节页签名）。
            # 逐个候选试，谁先给出 ``utEnc`` 就用谁 —— 不猜"哪个才对"。
            html = getattr(landing, "text", "") or ""
            hidden = dict(re.findall(r'id="(\w+)"[^>]*value="([0-9a-f]{32})"', html))
            candidates = [value for key, value in hidden.items() if key in {"oldenc", "enc"}]
            openc = hidden.get("openc", "")
            if not candidates:
                return ""
            for enc in candidates:
                url = (
                    f"{REGISTRY['chapter_study_page'].url}?chapterId={chapter_id}&courseId={course_id}"
                    f"&clazzid={clazz_id}&cpi={cpi}&enc={enc}&mooc2=1&hidetype=0&fanyaVersion=0"
                )
                if openc:
                    url += f"&openc={openc}"
                page = await self._transport.request(session, url, follow_redirects=True)
                found = re.search(
                    r'utEnc\s*=\s*["\']([0-9a-f]{32})["\']', getattr(page, "text", "") or ""
                )
                if found is not None:
                    return found.group(1)
            log.debug("章节页里没有 utEnc（课程 %s，试了 %d 个 enc）", course_id, len(candidates))
            return ""
        except Exception as exc:
            log.debug("取 utEnc 失败（课程 %s）：%s", course_id, exc)
            return ""

    async def refresh_quiz_tokens(
        self,
        account_id: int,
        *,
        course_id: str,
        clazz_id: str,
        chapter_id: str,
        work_id: str,
    ) -> dict[str, str]:
        """就地重新取测验票据（真机实测 2026-10-12：卡片里的 ``enc`` 几分钟就轮换）。

        扫描时存下的 ``enc`` / ``jtoken`` / ``utEnc`` 很快失效 —— 换票可能还能过，
        但**提交会被服务端拒**（回「作业提交失败！」），再久一点换票本身直接 403。
        正确做法是在执行前**就地把这张卡重扫一遍**，拿全新的票据；而不是信任
        扫描那一刻存进 payload 的值。

        路径与 :meth:`scan_points` 完全一致（入口 → 章节页 → 卡片）：按
        ``chapter_id`` 找章节、按 ``attachment.work_id`` 找附件，字段来源与
        :meth:`_store_points` 一一对应（``attachment.enc`` / ``attachment.work_id`` /
        ``card.defaults.*``，``utEnc`` 复用 :meth:`_fetch_utenc`）。

        :param account_id: 账号 ID
        :param course_id: 课程 ID
        :param clazz_id: 班级 ID
        :param chapter_id: 任务点所在章节 ID
        :param work_id: 作业/测验 ID（卡片上的 ``workid``）
        :returns: 新鲜票据（**只含非空项**）；任一步失败都返回 ``{}`` ——
            绝不抛异常出去，让调用方拿旧 payload 继续跑（失败要软）
        """
        try:
            session = self._session_for(account_id)
            entry_url = await self._api.enter_course(
                session, course_id=course_id, clazz_id=clazz_id
            )
            entry_query = dict(parse_qsl(urlparse(entry_url).query))
            cpi = entry_query.get("cpi", "")
            stuenc = extract_stuenc(entry_url)

            html = await self._chapter_api.fetch_content_html(
                session,
                course_id=course_id,
                clazz_id=clazz_id,
                cpi=cpi,
                stuenc=stuenc,
            )
            chapter = next(
                (item for item in parse_chapter_tree(html) if item.chapter_id == chapter_id),
                None,
            )
            if chapter is None:
                log.debug("刷新测验票据：课程 %s 里找不到章节 %s", course_id, chapter_id)
                return {}
            # ★ 一个章节有多个任务点（视频、章节测验…），刷新必须逐个 num 找 ——
            #   只取默认 num 会拿到视频那张卡、永远匹配不到测验的 workid，
            #   于是"刷新静默失败 → 用过期票据提交 → 平台回「作业提交失败！」"。
            card = None
            for num in range(10):
                candidate = await self._chapter_api.fetch_cards(
                    session,
                    course_id=course_id,
                    clazz_id=clazz_id,
                    knowledge_id=chapter.chapter_id,
                    cpi=cpi,
                    stuenc=stuenc,
                    num=num,
                )
                if candidate is None or not candidate.attachments:
                    break
                card = candidate
                if any(item.work_id == work_id for item in candidate.attachments):
                    break
            if card is None:
                log.debug("刷新测验票据：章节 %s 的卡片无法解析", chapter_id)
                return {}
            attachment = next((item for item in card.attachments if item.work_id == work_id), None)
            if attachment is None:
                log.debug("刷新测验票据：章节 %s 里找不到 workid=%s", chapter_id, work_id)
                return {}
            utenc = await self._fetch_utenc(
                session,
                entry_url=entry_url,
                chapter_id=chapter.chapter_id,
                course_id=course_id,
                clazz_id=clazz_id,
                cpi=cpi,
            )
            defaults = card.defaults
            fresh = {
                "enc": attachment.enc,
                "jtoken": attachment.jtoken,
                "ktoken": defaults.ktoken,
                "cpi": defaults.cpi or cpi,
                "clazzId": defaults.clazz_id or clazz_id,
                "userid": defaults.userid,
                "cardid": defaults.card_id,
                "workid": attachment.work_id,
                "utEnc": utenc,
            }
            return {key: value for key, value in fresh.items() if value}
        except Exception as exc:
            log.debug("刷新测验票据失败（课程 %s / workid=%s）：%s", course_id, work_id, exc)
            return {}

    async def _store_points(
        self,
        *,
        account_id: int,
        local_course_id: int,
        remote_course_id: str,
        chapter: object,
        card: JobCard,
        fallback_clazz: str,
        fallback_cpi: str,
        utenc: str = "",
        num: int = 0,
        report: PointScanReport,
    ) -> None:
        """把一个章节卡片里的任务点写入库（幂等：重复扫描只补字段）。"""
        chapter_id = str(getattr(chapter, "chapter_id", ""))
        chapter_index = int(getattr(chapter, "index", 0))
        defaults = card.defaults

        async with self._uow.begin() as db:
            repo = TaskPointRepo(db)
            for offset, attachment in enumerate(card.attachments):
                point_id = attachment.object_id or attachment.job_id
                if not point_id:
                    continue
                kind = attachment.kind
                # ★ 章内序号必须把卡片序号 num 算进去：一个章节的多个任务点分别来自
                #   num=0/1/2… 各自的卡片，只按卡内 offset 排序会让「视频」和「章节测验」
                #   撞在同一个 sequence 上 —— 顺序就退化成"看数据库插入顺序"，
                #   重扫一次就可能错位（用户要的是"视频完成 → 立刻做其后测验"）。
                sequence = chapter_index * 1000 + num * 100 + offset
                payload = {
                    "clazzId": defaults.clazz_id or fallback_clazz,
                    "userid": defaults.userid,
                    "cpi": defaults.cpi or fallback_cpi,
                    "courseId": remote_course_id,
                    "knowledgeid": chapter_id,
                    "otherInfo": attachment.other_info,
                    "jtoken": attachment.jtoken,
                    # 「阅读 / 专题阅读」卡的完成令牌：真机实测（2026-10-12）浏览器打开阅读卡时
                    # 用它调 /mooc-ans/coursedata/readjobv2/show 上报完成 —— 与 jtoken 是两条
                    # 并行的令牌线（白名单漏了它，执行器就永远拿不到，只能撞经典接口被判「无效的请求参数」）。
                    "enc": attachment.enc,
                    # 换票参数（课程级，见 _fetch_utenc）
                    "utEnc": utenc,
                    "ktoken": defaults.ktoken,
                    "cardid": defaults.card_id,
                    "reportUrl": defaults.report_url,
                    "attDuration": attachment.att_duration,
                    "attDurationEnc": attachment.att_duration_enc,
                    # ★ 必须是卡片上的 ``workid``（十六进制串）。早先写的是
                    # ``aid or mid`` —— ``mid`` 长得像数字，被当成 workid 存了下来，
                    # 换票时自然拿到一张 HTML 错误页（表现为 403）。
                    "workid": attachment.work_id,
                }
                existed = await repo.get(local_course_id, point_id) is not None
                await repo.upsert(
                    account_id=account_id,
                    course_id=local_course_id,
                    point_id=point_id,
                    job_id=attachment.job_id,
                    chapter_id=chapter_id,
                    kind=kind,
                    title=attachment.name,
                    sequence=sequence,
                    duration=attachment.att_duration,
                    payload=payload,
                )
                if existed:
                    report.updated += 1
                else:
                    report.created += 1
                report.kinds[kind] = report.kinds.get(kind, 0) + 1

    # ------------------------------------------------------------------ #
    # 读
    # ------------------------------------------------------------------ #
    async def list_courses(self, account_id: int, *, selected_only: bool = False) -> list[Course]:
        """列出本地课程缓存（不联网）。

        :param account_id: 账号 ID
        :param selected_only: 只列已勾选要刷的
        :returns: 课程列表
        """
        async with self._uow.read() as db:
            return list(
                await CourseRepo(db).list_for_account(account_id, selected_only=selected_only)
            )

    async def stats(self, account_id: int) -> dict[str, int]:
        """课程与任务点的概览（课程页顶部那几个数字）。"""
        async with self._uow.read() as db:
            courses = await CourseRepo(db).list_for_account(account_id)
            points = await TaskPointRepo(db).status_counts(account_id)
        return {
            "courses": len(courses),
            "selected": sum(1 for course in courses if course.selected),
            **points,
        }

    # ------------------------------------------------------------------ #
    # 写
    # ------------------------------------------------------------------ #
    async def select(self, account_id: int, course_keys: list[str], *, replace: bool = True) -> int:
        """勾选要刷的课程。

        勾选是**替换式**的（与 legacy 一致）：传一门课就等于"只刷这一门"。

        :param account_id: 账号 ID
        :param course_keys: 要勾选的课程键（``courseId:clazzId``）
        :param replace: True = 先把其它课取消勾选
        :returns: 最终被勾选的课程数
        """
        wanted = set(course_keys)
        async with self._uow.begin() as db:
            repo = CourseRepo(db)
            for course in await repo.list_for_account(account_id):
                should_select = course.course_key in wanted
                if course.selected != should_select and (should_select or replace):
                    course.selected = should_select
            await db.flush()
            selected = len(await repo.list_for_account(account_id, selected_only=True))
        log.info("账号 %s 勾选课程 %d 门", account_id, selected)
        return selected

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _session_for(self, account_id: int) -> SessionState:
        """取会话（没登录态就明确报错，别让它去撞 401）。"""
        if not self._registry.has(account_id):
            raise NotLoggedIn(f"账号 {account_id} 没有登录态，先执行 `cx auth login`")
        state = self._registry.get(account_id)
        if not state.cookies:
            raise SessionExpired(f"账号 {account_id} 的会话没有 cookie，需要重新登录")
        return state
