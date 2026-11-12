"""把 legacy 抓包产物转换成 tests/fixtures 下的可回归样本。

用法::

    uv run python scripts/import_legacy_fixtures.py

来源与去向（每一步都保留原始出处，便于日后对账）：

==============================  ============================================
legacy 抓包（data/debug/）       fixture（tests/fixtures/）
==============================  ============================================
course_list_*.txt                course_list.html     课程列表 HTML（GBK 实际编码）
cards_*.html                     job_card_video.json  任务点卡片里内嵌的 JSON
chapters_*.html                  chapter_page.html    章节框架页
==============================  ============================================

设计说明：**不做任何加工**，只做提取与转码 —— fixture 必须与线上原始响应一致，
否则回归测试会跑在一个"理想化"的世界里。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEBUG = ROOT / "data" / "debug"
FIXTURES = ROOT / "tests" / "fixtures"


def _read_text(path: Path) -> str:
    """读取抓包文本（学习通混杂 GBK/UTF-8，逐个尝试）。"""
    raw = path.read_bytes()
    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _extract_json_object(text: str, anchor: str) -> str | None:
    """从文本里抠出一段完整的 JSON 对象（按大括号配对，跳过字符串内的括号）。

    :param text: 页面源码
    :param anchor: 起点标记（例如 ``{"hiddenConfig"``）
    :returns: JSON 文本；找不到返回 None
    """
    start = text.find(anchor)
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def import_course_list() -> str | None:
    """课程列表 HTML。"""
    candidates = sorted(DEBUG.glob("course_list_*courselistdata*.txt"))
    if not candidates:
        return None
    target = FIXTURES / "course_list.html"
    target.write_text(_read_text(candidates[0]), encoding="utf-8", newline="\n")
    return f"{candidates[0].name} -> {target.relative_to(ROOT)}"


def import_job_card() -> str | None:
    """任务点卡片里内嵌的 JSON（视频类）。"""
    candidates = sorted(DEBUG.glob("cards_*.html"))
    for path in candidates:
        text = _read_text(path)
        for anchor in ('{"hiddenConfig"', '{"attachments"'):
            blob = _extract_json_object(text, anchor)
            if blob is None:
                continue
            try:
                parsed = json.loads(blob)
            except json.JSONDecodeError:
                continue
            target = FIXTURES / "job_card_video.json"
            target.write_text(
                json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
            )
            return f"{path.name} -> {target.relative_to(ROOT)}"
    return None


def import_chapter_page() -> str | None:
    """章节框架页。"""
    candidates = sorted(DEBUG.glob("chapters_*.html"))
    if not candidates:
        return None
    target = FIXTURES / "chapter_page.html"
    target.write_text(_read_text(candidates[0]), encoding="utf-8", newline="\n")
    return f"{candidates[0].name} -> {target.relative_to(ROOT)}"


def import_quiz_page() -> str | None:
    """已保存的题目页（若存在）。"""
    candidates = sorted(DEBUG.glob("quiz_*.html"))
    if not candidates:
        return None
    target = FIXTURES / "quiz_page.html"
    target.write_text(_read_text(candidates[0]), encoding="utf-8", newline="\n")
    return f"{candidates[0].name} -> {target.relative_to(ROOT)}"


STEPS = (
    ("course_list", import_course_list),
    ("job_card", import_job_card),
    ("chapter_page", import_chapter_page),
    ("quiz_page", import_quiz_page),
)


def main() -> int:
    """执行全部导入，打印结果。"""
    if not DEBUG.exists():
        print(f"找不到抓包目录：{DEBUG}")
        return 1
    FIXTURES.mkdir(parents=True, exist_ok=True)

    imported = 0
    for name, step in STEPS:
        result = step()
        if result is None:
            print(f"[skip] {name}：找不到对应的 legacy 抓包")
        else:
            imported += 1
            print(f"[ok]   {name}：{result}")

    print(f"\n共导入 {imported} 个 fixture -> {FIXTURES.relative_to(ROOT)}")
    return 0 if imported else 1


if __name__ == "__main__":
    sys.exit(main())
