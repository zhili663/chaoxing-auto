"""测验验证码接力端点（取图 + 带码重试）。

真机背景（2026-10-16）：某份答卷被服务端转入「要求人工验证码」态后，交卷一律回
「验证码不正确，请重新输入」。`StudyService._run_quiz` 会把验证码图片落盘到
``data/captcha/quiz-<workid>-<ts>.jpg`` 并抛 ``HumanRequired``（``_dispatch`` 因此把
任务点标成 ``need_human``，消息里带图片路径）。

前端拿到 ``need_human`` 的点后：先 ``GET`` 本文件的第一支端点取图给人看，
再 ``POST`` 第二支端点把人工读出的码带进去重试 —— 这就是"验证码接力"。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from chaoxing.api.deps import AuthContext, get_container, load_account, require
from chaoxing.api.errors import ApiError, NotFound
from chaoxing.api.security import require_same_origin
from chaoxing.client.exceptions import HumanRequired
from chaoxing.config import get_settings
from chaoxing.core.container import Container
from chaoxing.core.services.study import PointView

router = APIRouter(tags=["study"])


class CaptchaSubmit(BaseModel):
    """人工读出的验证码。"""

    input_code: str = Field(min_length=1, max_length=16, description="验证码图片上的字符")


def _captcha_dir() -> Path:
    """验证码图片目录（与 ``cli/auth.py`` 的约定一致）。"""
    return get_settings().app.data_dir / "captcha"


async def _point_or_404(container: Container, point_id: int) -> PointView:
    point = await container.study.get_point(point_id)
    if point is None:
        raise NotFound("point_not_found", f"任务点 {point_id} 不存在")
    return point


@router.get("/study/points/{point_id}/captcha")
async def get_quiz_captcha(
    point_id: int,
    ctx: AuthContext = Depends(require("study:record:read")),
    container: Container = Depends(get_container),
) -> FileResponse:
    """取该任务点**最新**的验证码图片（没有就 404）。"""
    point = await _point_or_404(container, point_id)
    await load_account(point.account_id, ctx, container)
    work_id = str((point.payload or {}).get("workid", "") or point.point_id)
    directory = _captcha_dir()
    candidates = (
        sorted(directory.glob(f"quiz-{work_id}-*.jpg"), reverse=True) if directory.exists() else []
    )
    if not candidates:
        raise NotFound("captcha_not_found", f"任务点 {point_id} 还没有验证码图片")
    return FileResponse(candidates[0], media_type="image/jpeg")


@router.post("/study/points/{point_id}/captcha")
async def submit_quiz_captcha(
    point_id: int,
    payload: CaptchaSubmit,
    request: Request,
    ctx: AuthContext = Depends(require("study:plan:run")),
    container: Container = Depends(get_container),
) -> dict[str, str]:
    """带上人工验证码重试这个任务点。"""
    require_same_origin(request)
    point = await _point_or_404(container, point_id)
    await load_account(point.account_id, ctx, container)
    try:
        status = await container.study.run_quiz(
            point.account_id, point_id, input_code=payload.input_code.strip()
        )
    except HumanRequired as exc:
        # 码不对 / 过期：把服务端原话透出去，让人再试一次
        raise ApiError("captcha_rejected", str(exc)) from exc
    return {"status": status}
