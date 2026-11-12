"""课程域的请求/响应模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CourseOut(BaseModel):
    """课程信息。"""

    id: int
    account_id: int
    course_key: str
    title: str
    teacher: str
    selected: bool
    enabled: bool
    priority: int
    note: str
    scanned_at: datetime | None = None


class TaskPointOut(BaseModel):
    """任务点概要（**不含 payload**：里面全是签名材料，前端不需要）。"""

    id: int
    course_id: int
    point_id: str
    job_id: str
    kind: str
    title: str
    status: str
    progress: float
    duration: float
    sequence: int
    retry_count: int
    last_error: str


class ScanRequest(BaseModel):
    """扫描课程列表。"""

    account_id: int = Field(ge=1)


class PointScanRequest(BaseModel):
    """扫描某门课的章节与任务点。"""

    account_id: int = Field(ge=1)
    course_key: str = Field(min_length=1, description="courseId:clazzId")


class SelectRequest(BaseModel):
    """勾选要刷的课程（替换式）。"""

    account_id: int = Field(ge=1)
    course_keys: list[str] = Field(default_factory=list)


class SelectResult(BaseModel):
    """勾选结果。"""

    selected: int
    course_keys: list[str] = Field(default_factory=list)


class ScanResult(BaseModel):
    """课程扫描结果。"""

    total: int
    created: int
    updated: int


class PointScanResult(BaseModel):
    """任务点扫描结果。"""

    chapters: int
    created: int
    updated: int
    kinds: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
