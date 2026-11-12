"""HTML / JSON 解析层：纯函数，零副作用，可离线全量回归。

为什么单独一层
--------------
学习通的页面结构会随版本漂移，解析逻辑因此最需要**可测**：这一层只做
"文本 → 模型"的转换，不碰网络、不碰存储，测试可以直接喂真实抓包样本
（``tests/fixtures/``）。

策略沿用 legacy 被验证有效的两招：
1. **候选字段**：同一语义在不同版本里叫法不同，逐个尝试（现在是类型里的 AliasChoices）；
2. **嵌套摊平**：``attachments[].property`` 里藏着 ``_jobid`` 等关键字段。
"""

from __future__ import annotations

from chaoxing.client.parser.card import (
    extract_cards_payload,
    flatten_property,
    merge_attachments,
    parse_job_card,
)
from chaoxing.client.parser.common import attr, extract_json_object, to_float
from chaoxing.client.parser.course import parse_course_list

__all__ = [
    "attr",
    "extract_cards_payload",
    "extract_json_object",
    "flatten_property",
    "merge_attachments",
    "parse_course_list",
    "parse_job_card",
    "to_float",
]
