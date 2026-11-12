"""权限元测试：漏挂鉴权的路由必须让 CI 失败。

来源：``docs/REFACTOR_PLAN.md`` §5.8 的硬要求 —— **每个路由都要挂权限节点，
读路由同样要挂**。靠人眼 review 一定会漏（尤其是后来新增的路由），
所以让测试遍历路由表来查，把"记得加鉴权"变成机器约束。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.routing import APIRoute

#: 允许公开访问的路径（**按后缀匹配**：``include_router`` 加的 ``/api`` 前缀
#: 不会体现在 ``route.path`` 上，用后缀比硬拼前缀更稳）
PUBLIC_SUFFIXES = (
    "/health",
    "/auth/challenge",
    "/auth/register",
    "/auth/login",
    "/auth/logout",
)

#: 框架自带、不需要业务鉴权的路径前缀
FRAMEWORK_PREFIXES = ("/api/docs", "/api/openapi.json", "/openapi.json", "/docs", "/redoc")

#: 规范里定义的权限节点（拼错一个就等于挂了不存在的权限 —— 逻辑上永久拒绝）
KNOWN_PERMISSIONS = {
    "account:read",
    "account:read:all",
    "account:write",
    "account:delete",
    "account:login",
    "group:read",
    "group:write",
    "course:read",
    "course:scan",
    "course:write",
    "course:delete",
    "course:select",
    "study:plan:read",
    "study:plan:write",
    "study:plan:run",
    "study:plan:control",
    "study:record:read",
    "quiz:read",
    "quiz:write",
    "quiz:submit",
    "quiz:cache:write",
    "user:read",
    "user:write",
    "role:read",
    "role:manage",
    "settings:read",
    "settings:write",
    "prompt:write",
    "system:audit",
    "system:logs",
    "system:task",
    "system:doctor",
    "stream:subscribe",
}


#: 只要求**登录**、不要求具体权限节点的路径（"我是谁"与"前端渲染所需的元数据"）
LOGIN_ONLY_SUFFIXES = ("/auth/me", "/meta/enums", "/meta/point-kinds", "/meta/permissions")


def is_public(path: str) -> bool:
    """是否是公开路径（按后缀判定，理由见 ``PUBLIC_SUFFIXES`` 的注释）。"""
    return any(path.endswith(suffix) for suffix in PUBLIC_SUFFIXES)


def is_login_only(path: str) -> bool:
    """是否只需登录（不校验权限节点）。"""
    return any(path.endswith(suffix) for suffix in LOGIN_ONLY_SUFFIXES)


def has_login_dependency(route: APIRoute) -> bool:
    """路由是否挂了"必须登录"的依赖。"""
    stack = [route.dependant]
    while stack:
        node = stack.pop()
        if getattr(node.call, "__name__", "") in {"auth_context", "require_user"}:
            return True
        stack.extend(node.dependencies)
    return False


def declared_requires(route: APIRoute) -> list[str]:
    """收集一条路由上声明的权限节点（含嵌套依赖）。"""
    found: list[str] = []
    stack = [route.dependant]
    while stack:
        node = stack.pop()
        requires = getattr(node.call, "__requires__", None)
        if requires:
            found.extend(str(item) for item in requires)
        stack.extend(node.dependencies)
    return found


#: 嵌套路由可能挂在这些属性上（不同 FastAPI 版本不一样）
_NESTED_ATTRS = ("routes", "original_router", "router", "app")


def _nested_routes(item: object) -> list[object] | None:
    """取一个容器对象里的子路由列表（兼容各版本 FastAPI 的包装）。"""
    for attr in _NESTED_ATTRS:
        value = getattr(item, attr, None)
        if value is None:
            continue
        routes = getattr(value, "routes", None)
        if isinstance(routes, list) and routes:
            return list(routes)
    return None


def iter_api_routes(app: FastAPI) -> list[APIRoute]:
    """递归展开应用的全部 API 路由。

    新版 FastAPI（0.14x）会把 ``include_router`` 的产物包一层 ``_IncludedRouter``，
    直接扫 ``app.routes`` 一个真实端点都看不到 —— **测试会静默全绿**，
    所以这里必须递归进去（下面的 ``test_route_scan_actually_sees_endpoints``
    就是专门盯这件事的）。
    """
    found: list[APIRoute] = []
    stack: list[object] = list(app.routes)
    while stack:
        item = stack.pop()
        if isinstance(item, APIRoute):
            found.append(item)
            continue
        nested = _nested_routes(item)
        if nested:
            stack.extend(nested)
    return found


#: 静态托管路由（SPA fallback）：转发文件，不涉及业务数据，因此不挂权限节点
STATIC_ROUTE_NAMES = {"spa_fallback"}


def business_routes(app: FastAPI) -> list[APIRoute]:
    """筛出业务路由（去掉框架文档、公开路径、登录-only 与静态托管）。"""
    result: list[APIRoute] = []
    for route in iter_api_routes(app):
        if route.name in STATIC_ROUTE_NAMES:
            continue
        if is_public(route.path) or is_login_only(route.path):
            continue
        if route.path.startswith(FRAMEWORK_PREFIXES):
            continue
        result.append(route)
    return result


def test_every_protected_route_declares_permissions(app: FastAPI) -> None:
    """除公开路径外，每条路由都必须声明权限节点。"""
    offenders = [
        f"{sorted(route.methods)} {route.path}"
        for route in business_routes(app)
        if not declared_requires(route)
    ]

    assert not offenders, f"这些路由没挂权限节点（读路由同样要挂）：{offenders}"


def test_login_only_routes_still_require_authentication(app: FastAPI) -> None:
    """ "只需登录"的路由也必须真的挂了登录依赖 —— 否则它就悄悄变成公开接口了。"""
    routes = [route for route in iter_api_routes(app) if is_login_only(route.path)]

    assert routes, "登录-only 白名单里没有对应路由，白名单该清理了"
    offenders = [
        f"{sorted(route.methods)} {route.path}"
        for route in routes
        if not has_login_dependency(route)
    ]
    assert not offenders, f"这些路由声称要登录，却没挂登录依赖：{offenders}"


def test_declared_permissions_are_known_nodes(app: FastAPI) -> None:
    """声明的权限节点必须在规范清单里（拼错了会永久拒绝访问）。"""
    declared = {item for route in business_routes(app) for item in declared_requires(route)}

    unknown = declared - KNOWN_PERMISSIONS
    assert not unknown, f"出现了规范外的权限节点：{sorted(unknown)}"


def test_public_paths_really_exist(app: FastAPI) -> None:
    """白名单里的路径必须真的存在于路由表（避免白名单写成历史遗留）。"""
    paths = {route.path for route in iter_api_routes(app)}
    missing = [s for s in PUBLIC_SUFFIXES if not any(p.endswith(s) for p in paths)]

    assert not missing, f"白名单里有不存在的路径（该清理了）：{missing}"


def test_route_scan_actually_sees_endpoints(app: FastAPI) -> None:
    """元测试自身的前提：路由扫描必须真的看到端点。

    这条是防"扫描写错导致全部测试静默通过"的 —— 它自己坏了比漏挂鉴权更危险。
    """
    routes = iter_api_routes(app)

    assert len(routes) >= 6, f"只扫到 {len(routes)} 条路由，扫描逻辑可能失效了"
