"""仓储层：课程勾选不被重扫覆盖、任务点顺序、账号状态流转。

来源：legacy 的两个真实需求 ——
① 重新扫描课程后，用户已经勾选的课不能被清掉（否则每天都要重新勾一遍）；
② 任务点必须按"章序号 × 1000 + 章内序号"的顺序执行（视频后紧跟的题先做完）。
"""

from __future__ import annotations

from chaoxing.storage.repositories import AccountRepo, CourseRepo, TaskPointRepo
from chaoxing.storage.uow import UnitOfWork


async def test_account_crud_and_priority_order(uow: UnitOfWork) -> None:
    """账号按 priority 排序，停用后仍可查到但会被标记。"""
    async with uow.begin() as session:
        repo = AccountRepo(session)
        await repo.create(username="late", priority=200)
        await repo.create(username="early", priority=10)

    async with uow.read() as session:
        accounts = await AccountRepo(session).list_all()

    assert [a.username for a in accounts] == ["early", "late"]

    async with uow.begin() as session:
        repo = AccountRepo(session)
        target = (await repo.list_all())[0]
        assert await repo.set_enabled(target.id, enabled=False)
        assert await repo.update_state(target.id, status="logged_in")

    async with uow.read() as session:
        repo = AccountRepo(session)
        # 只停用了 priority=10 的那个，另一个仍在启用列表里
        assert [a.username for a in await repo.list_all(include_disabled=False)] == ["late"]
        refreshed = (await repo.list_all())[0]
        assert refreshed.status == "logged_in"


async def test_course_scan_does_not_clear_user_selection(uow: UnitOfWork, account_id: int) -> None:
    """重扫只能更新标题，**绝不能覆盖用户的勾选与优先级**。"""
    async with uow.begin() as session:
        repo = CourseRepo(session)
        course = await repo.upsert_from_scan(account_id, course_key="100:200", title="高等数学")
        await repo.set_selected(course.id, selected=True)
        course.priority = 5

    async with uow.begin() as session:
        await CourseRepo(session).upsert_from_scan(
            account_id, course_key="100:200", title="高等数学（新版）"
        )

    async with uow.read() as session:
        stored = await CourseRepo(session).get_by_key(account_id, "100:200")

    assert stored is not None
    assert stored.title == "高等数学（新版）"  # 标题会更新
    assert stored.selected is True  # 勾选保留
    assert stored.priority == 5  # 优先级保留


async def test_course_soft_delete_hides_from_list(uow: UnitOfWork, account_id: int) -> None:
    """软删后列表不再返回，但记录还在（历史统计要能对得上）。"""
    async with uow.begin() as session:
        repo = CourseRepo(session)
        course = await repo.upsert_from_scan(account_id, course_key="1:1", title="待删")
        await repo.soft_delete(course.id)

    async with uow.read() as session:
        assert await CourseRepo(session).list_for_account(account_id) == []


async def test_task_points_ordered_by_sequence(uow: UnitOfWork, account_id: int) -> None:
    """按 sequence 升序取待处理任务点（"视频 + 紧随其后的题"的顺序保证）。"""
    async with uow.begin() as session:
        course = await CourseRepo(session).upsert_from_scan(
            account_id, course_key="9:9", title="顺序课"
        )
        repo = TaskPointRepo(session)
        await repo.upsert(
            account_id=account_id,
            course_id=course.id,
            point_id="p1003",
            kind="video",
            sequence=1003,
        )
        await repo.upsert(
            account_id=account_id,
            course_id=course.id,
            point_id="p1001",
            kind="video",
            sequence=1001,
        )
        await repo.upsert(
            account_id=account_id,
            course_id=course.id,
            point_id="p1002",
            kind="quiz",
            sequence=1002,
        )

    async with uow.read() as session:
        pending = await TaskPointRepo(session).list_pending(account_id, course.id)

    assert [point.point_id for point in pending] == ["p1001", "p1002", "p1003"]


async def test_task_point_upsert_keeps_progress_and_payload(
    uow: UnitOfWork, account_id: int
) -> None:
    """重扫卡片时不能把已上报的进度抹掉，payload 要能合并新增字段。"""
    async with uow.begin() as session:
        course = await CourseRepo(session).upsert_from_scan(
            account_id, course_key="7:7", title="进度课"
        )
        repo = TaskPointRepo(session)
        point = await repo.upsert(
            account_id=account_id,
            course_id=course.id,
            point_id="v1",
            kind="video",
            sequence=1,
            payload={"otherInfo": "nodeId_1"},
        )
        await repo.mark(point.id, status="running", progress=120.0)

    async with uow.begin() as session:
        await TaskPointRepo(session).upsert(
            account_id=account_id,
            course_id=course.id,
            point_id="v1",
            kind="video",
            sequence=1,
            payload={"jtoken": "new-token"},
        )

    async with uow.read() as session:
        # 直接查（list_pending 只回 queued/failed，这里要看的是被标记成 running 的那条）
        from sqlalchemy import select

        from chaoxing.storage.models import TaskPoint

        stored = (await session.scalars(select(TaskPoint).where(TaskPoint.point_id == "v1"))).one()

    assert stored.progress == 120.0
    assert stored.payload["otherInfo"] == "nodeId_1"
    assert stored.payload["jtoken"] == "new-token"


async def test_status_counts(uow: UnitOfWork, account_id: int) -> None:
    """按状态统计（进度面板的数据源）。"""
    async with uow.begin() as session:
        course = await CourseRepo(session).upsert_from_scan(
            account_id, course_key="3:3", title="统计课"
        )
        repo = TaskPointRepo(session)
        done = await repo.upsert(
            account_id=account_id, course_id=course.id, point_id="a", sequence=1
        )
        await repo.upsert(account_id=account_id, course_id=course.id, point_id="b", sequence=2)
        await repo.mark(done.id, status="done", progress=100.0)

    async with uow.read() as session:
        counts = await TaskPointRepo(session).status_counts(account_id)

    assert counts == {"done": 1, "queued": 1}
