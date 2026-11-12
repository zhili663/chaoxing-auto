"""注册与口令相关端点。

路由只做三件事：**过安全门 → 调服务 → 转响应**（``docs/REFACTOR_PLAN.md`` §5.11 规则 3）。
业务判定（首个用户即超管、开关、持久化）都在 ``core.services`` 里，路由不重复实现。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from chaoxing.api.deps import get_container
from chaoxing.api.errors import ApiError, Conflict, Forbidden
from chaoxing.api.routers.auth.schemas import ChallengeResponse, RegisterRequest, UserOut
from chaoxing.api.routers.auth.session import to_user_out
from chaoxing.api.security import require_same_origin
from chaoxing.core.container import Container
from chaoxing.core.services.users import UserExists
from chaoxing.utils.security import NONCE_TTL, looks_like_client_secret

router = APIRouter(tags=["auth"])


@router.get("/auth/challenge")
async def issue_challenge(
    scene: str = "register", container: Container = Depends(get_container)
) -> ChallengeResponse:
    """签发一次性 nonce（注册与改密需要）。"""
    return ChallengeResponse(nonce=container.nonces.issue(scene), ttl=NONCE_TTL, scene=scene)


async def _guard_registration(payload: RegisterRequest, container: Container) -> None:
    """注册前的三道门：口令形态 → 首个用户 → 开关与 nonce。"""
    if not looks_like_client_secret(payload.secret):
        raise ApiError("invalid_secret", "口令必须是前端 sha256 后的 64 位十六进制字符串")

    if await container.users.is_first_user():
        return  # 首个用户永远放行（否则新部署没人能授权）
    if not container.settings.app.allow_registration:
        raise Forbidden("已关闭公开注册，请联系管理员创建账号")
    if not container.nonces.consume(payload.nonce, "register"):
        raise ApiError("invalid_nonce", "注册凭证已失效，请刷新页面重试")


@router.post("/auth/register", status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest, request: Request, container: Container = Depends(get_container)
) -> UserOut:
    """注册用户（首个用户自动成为超管）。"""
    require_same_origin(request)
    await _guard_registration(payload, container)

    try:
        user = await container.users.create(
            username=payload.username,
            client_secret=payload.secret,
            display_name=payload.display_name,
            phone=payload.phone,
        )
    except UserExists as exc:
        raise Conflict("user_exists", str(exc)) from exc
    except ValueError as exc:
        raise ApiError("invalid_secret", str(exc)) from exc

    return to_user_out(user, await container.users.permissions(user))
