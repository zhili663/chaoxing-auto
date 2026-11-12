"""``python -m chaoxing`` 入口（与 ``cx`` 控制台脚本等价）。

``docs/REFACTOR_PLAN.md`` §3 的目标目录树里要求有这一层 —— 容器里跑东西时
（systemd、CI、别人的脚本）不一定会先 ``pip install`` 出 ``cx`` 这个入口点。
"""

from __future__ import annotations

from chaoxing.cli.main import main

if __name__ == "__main__":
    main()
