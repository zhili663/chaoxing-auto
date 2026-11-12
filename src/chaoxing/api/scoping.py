"""账号范围收敛：把请求里的 ``account_id`` 变成"调用者有权看的那个"。

三条口径（study / plans / stats 三处路由共用一份）：

* 管理员（``account:read:all``）不指定账号 = **全局**；
* 普通用户不指定账号 = **明确报错** —— 否则"不传参数"等于把全库合计送给他；
* 越权访问按 **404** 处理（403 等于确认资源存在）。
"""

from __future__ import annotations

from chaoxing.api.deps import AuthContext, load_account
from chaoxing.api.errors import ApiError
from chaoxing.core.container import Container

__all__ = ["scoped_account"]


async def scoped_account(
    account_id: int | None, ctx: AuthContext, container: Container
) -> int | None:
    """收敛账号范围。

    :param account_id: 请求里的账号（``None`` = 不限）
    :param ctx: 鉴权上下文
    :param container: 容器
    :raises ApiError: 普通用户没指定账号
    :raises NotFound: 指定了不属于自己的账号（与不存在同样处理）
    :returns: ``None`` = 全局（仅管理员可达）
    """
    if account_id is None:
        if ctx.sees_everything:
            return None
        raise ApiError("account_required", "请指定账号（普通用户只能查看自己的数据）")
    await load_account(account_id, ctx, container)
    return account_id
