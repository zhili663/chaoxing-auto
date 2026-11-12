"""CLI 运行时辅助：把"构造容器 → 执行 → 收尾"这段样板收敛到一处。

每个命令都遵循同一套流程，因此出错时的资源回收行为也一致 ——
不用每个命令各写一遍 ``try/finally``。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from chaoxing.core.container import Container


def run_with_container[T](action: Callable[[Container], Awaitable[T]]) -> T:
    """在完整容器里跑一段异步逻辑。

    :param action: 接收已装配的容器、返回结果
    :returns: ``action`` 的返回值
    """

    async def _main() -> T:
        container = await Container.create()
        try:
            return await action(container)
        finally:
            await container.aclose()

    return asyncio.run(_main())
