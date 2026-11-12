"""课程端点：列表、扫描、任务点扫描、勾选。

"刷什么"在这里**只被读取与修改，不被定义** —— 没有任何默认课程，
勾选状态只由用户操作产生（零硬编码原则在 API 层的落点）。

响应映射统一放在模块级函数里：路由只负责"鉴权 → 调服务 → 映射"，
handler 保持在一个屏以内（``scripts/check_router_size.py`` 会盯着这条）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from chaoxing.api.deps import AuthContext, get_container, load_account, require
from chaoxing.api.errors import ApiError, Conflict, NotFound
from chaoxing.api.routers.course.schemas import (
    CourseOut,
    PointScanRequest,
    PointScanResult,
    ScanRequest,
    ScanResult,
    SelectRequest,
    SelectResult,
    TaskPointOut,
)
from chaoxing.api.security import require_same_origin
from chaoxing.core.container import Container
from chaoxing.core.services.courses import CourseNotFound, NotLoggedIn, ScanInProgress
from chaoxing.storage.models import Course, TaskPoint

router = APIRouter(tags=["course"])


def to_course_out(item: Course) -> CourseOut:
    """课程 ORM → 响应模型。"""
    return CourseOut(
        id=item.id,
        account_id=item.account_id,
        course_key=item.course_key,
        title=item.title,
        teacher=item.teacher,
        selected=item.selected,
        enabled=item.enabled,
        priority=item.priority,
        note=item.note,
        scanned_at=item.scanned_at,
    )


def to_point_out(row: TaskPoint) -> TaskPointOut:
    """任务点 ORM → 响应模型（**不含 payload**：里面是签名材料）。"""
    return TaskPointOut(
        id=row.id,
        course_id=row.course_id,
        point_id=row.point_id,
        job_id=row.job_id,
        kind=row.kind,
        title=row.title,
        status=row.status,
        progress=row.progress,
        duration=row.duration,
        sequence=row.sequence,
        retry_count=row.retry_count,
        last_error=row.last_error,
    )


async def load_course(course_id: int, ctx: AuthContext, container: Container) -> Course:
    """按 ID 取课程并做归属校验（同账号可见性口径：越权一律 404）。"""
    async with container.uow.read() as db:
        course = await db.get(Course, course_id)
    if course is None or course.deleted_at is not None:
        raise NotFound("course_not_found", f"课程 {course_id} 不存在")
    await load_account(course.account_id, ctx, container)
    return course


async def scan_course_points(
    container: Container, account_id: int, course_key: str
) -> PointScanResult:
    """扫描一门课的任务点并把已知异常翻成 HTTP 错误（路由只保留鉴权与调用）。"""
    try:
        report = await container.courses.scan_points(account_id, course_key)
    except ScanInProgress as exc:
        raise Conflict("scan_in_progress", str(exc)) from exc
    except NotLoggedIn as exc:
        raise ApiError("not_logged_in", str(exc)) from exc
    except CourseNotFound as exc:
        raise NotFound("course_not_found", str(exc)) from exc
    return PointScanResult(
        chapters=report.chapters,
        created=report.created,
        updated=report.updated,
        kinds=report.kinds,
        warnings=report.warnings,
    )


@router.get("/courses")
async def list_courses(
    account_id: int,
    selected_only: bool = False,
    ctx: AuthContext = Depends(require("course:read")),
    container: Container = Depends(get_container),
) -> list[CourseOut]:
    """列出某账号下的课程（不联网，读本地缓存）。"""
    await load_account(account_id, ctx, container)
    courses = await container.courses.list_courses(account_id, selected_only=selected_only)
    return [to_course_out(item) for item in courses]


@router.get("/courses/{course_id}/points")
async def list_points(
    course_id: int,
    limit: int = 200,
    ctx: AuthContext = Depends(require("course:read")),
    container: Container = Depends(get_container),
) -> list[TaskPointOut]:
    """列出课程下的任务点（按播放顺序）。"""
    await load_course(course_id, ctx, container)
    rows = await container.study.list_points(course_id, limit=limit)
    return [to_point_out(row) for row in rows]


@router.post("/courses/scan")
async def scan_courses(
    payload: ScanRequest,
    request: Request,
    ctx: AuthContext = Depends(require("course:scan")),
    container: Container = Depends(get_container),
) -> ScanResult:
    """从学习通拉取该账号的课程列表并写库。"""
    require_same_origin(request)
    await load_account(payload.account_id, ctx, container)
    try:
        report = await container.courses.scan(payload.account_id)
    except ScanInProgress as exc:
        raise Conflict("scan_in_progress", str(exc)) from exc
    except NotLoggedIn as exc:
        raise ApiError("not_logged_in", str(exc)) from exc
    return ScanResult(total=report.total, created=report.created, updated=report.updated)


@router.post("/courses/points")
async def scan_points(
    payload: PointScanRequest,
    request: Request,
    ctx: AuthContext = Depends(require("course:scan")),
    container: Container = Depends(get_container),
) -> PointScanResult:
    """扫描一门课的章节与任务点（刷课前的必要一步）。"""
    require_same_origin(request)
    await load_account(payload.account_id, ctx, container)
    return await scan_course_points(container, payload.account_id, payload.course_key)


@router.post("/courses/select")
async def select_courses(
    payload: SelectRequest,
    request: Request,
    ctx: AuthContext = Depends(require("course:select")),
    container: Container = Depends(get_container),
) -> SelectResult:
    """勾选要刷的课程（**替换式**：传一门课就等于只刷这一门，与 CLI 口径一致）。"""
    require_same_origin(request)
    await load_account(payload.account_id, ctx, container)
    selected = await container.courses.select(payload.account_id, payload.course_keys)
    return SelectResult(selected=selected, course_keys=list(payload.course_keys))
