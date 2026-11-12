"""协议覆盖对账：端点注册表 ↔ 真实录制请求 ↔ 测试 fixture。

见 ``docs/REFACTOR_PLAN.md`` §5.14 —— **未建模调用是一等公民**：
抓包工具录到的每一个请求，要么能对上已注册的端点，要么出现在"未建模"清单里，
不允许悄悄略过。``build_markdown`` 的产物直接写进 ``docs/PROTOCOL_COVERAGE.md``。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlsplit, urlunsplit

from chaoxing.client.endpoints import REGISTRY
from chaoxing.client.endpoints.base import Calibration, Endpoint

#: 学习通各子系统域名（用于从录制流量里筛出本项目关心的请求）
CHAOXING_SUFFIXES = ("chaoxing.com", "chaoxing.cn")


@dataclass(frozen=True, slots=True)
class CoverageRow:
    """一行覆盖记录。

    :param endpoint: 端点
    :param fixture: 对应的测试样本文件名（无则是空串）
    """

    endpoint: Endpoint
    fixture: str = ""


def _is_chaoxing_url(url: str) -> bool:
    """是否学习通的请求（其它域的静态资源不关心）。"""
    host = urlparse(url).hostname or ""
    return any(host.endswith(suffix) for suffix in CHAOXING_SUFFIXES)


def _endpoint_path(spec: Endpoint) -> str:
    """端点 URL 的路径部分（去掉 host 与占位符的具体值）。"""
    return urlparse(spec.url).path


def match_fixtures(endpoints: Iterable[Endpoint], fixtures_dir: Path | None) -> dict[str, str]:
    """按文件名约定把 fixture 匹配到端点。

    约定：fixture 文件名去掉扩展名后，与端点名或端点名的语义片段一致
    （例如 ``course_list.html`` ↔ 端点 ``course_list``；``job_card_video.json`` ↔
    章节卡片端点）。匹配不上的端点会出现在报告里，正好提醒"该补样本了"。

    :param endpoints: 端点集合
    :param fixtures_dir: fixture 目录（不存在时全部视为未覆盖）
    :returns: 端点名 -> fixture 文件名
    """
    if fixtures_dir is None or not fixtures_dir.exists():
        return {}

    stems = {path.stem.lower(): path.name for path in fixtures_dir.iterdir() if path.is_file()}
    mapping: dict[str, str] = {}

    # 显式的语义别名（fixture 名与端点名不同但确实对应）
    aliases = {
        "chapter_cards": ("job_card", "cards", "chapter_page"),
        "course_list": ("course_list",),
        "quiz_detail": ("quiz_page",),
        "quiz_plain_questions": ("quiz_page",),
    }

    for spec in endpoints:
        candidates = (spec.name, *aliases.get(spec.name, ()))
        for candidate in candidates:
            for stem, filename in stems.items():
                if stem.startswith(candidate) or candidate in stem:
                    mapping[spec.name] = filename
                    break
            if spec.name in mapping:
                break
    return mapping


#: 看起来像 ID / token 的路径段（真机录制的 dtoken 是 32 位 hex、cpi 是 9 位数字）
_ID_SEGMENT_PATTERNS = (
    re.compile(r"^\d{3,}$"),
    re.compile(r"^[0-9a-fA-F]{16,}$"),
)

#: 「长且含数字」也算 ID（覆盖 base64url 形态的 token）
_ID_SEGMENT_MIN_LENGTH = 20


def harvest_urls(payload: object) -> list[str]:
    """递归抽取任意抓包结构里的 http(s) URL（保序、不崩）。

    临时调试 json 的形态没有约定（``data/debug/*.json`` 就是三种不同的形状），
    所以这里只认一件事：字符串长得像 URL 就收下。

    :param payload: 任意 JSON 结构
    :returns: 按出现顺序排列的 URL 列表（不做去重）
    """
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, str):
            if node.startswith(("http://", "https://")):
                found.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return found


def _mask_segment(segment: str) -> str:
    """把形如 ID / token 的路径段替换成 ``{id}``。

    「长且含数字」才算 —— 纯字母的长段是**接口名**（``studentstudycourselist``、
    ``addStudentWorkNewWeb``），抹掉它会让对账全盘失败。
    """
    if any(pattern.match(segment) for pattern in _ID_SEGMENT_PATTERNS):
        return "{id}"
    if len(segment) >= _ID_SEGMENT_MIN_LENGTH and any(char.isdigit() for char in segment):
        return "{id}"
    return segment


def sanitize_url(url: str) -> str:
    """把抓到的 URL 归一化成可入库的形状。

    规则（逐段判断 path）：

    * 丢掉 query 与 fragment —— 那里面全是 cookie 级参数（``userid`` / ``otherInfo``）；
    * 段满足 ``^\\d{3,}$``、``^[0-9a-fA-F]{16,}$``，或「长度 ≥20 且至少含一个数字」
      → 替换成 ``{id}``；
    * 其余原样保留。

    :param url: 抓到的完整 URL
    :returns: 只有形状、没有标识的 URL
    """
    parts = urlsplit(url)
    path = "/".join(_mask_segment(segment) for segment in parts.path.split("/"))
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def sanitize_urls(urls: Sequence[str]) -> list[str]:
    """批量脱敏 + 去重（保持首次出现顺序）。"""
    seen: set[str] = set()
    sanitized: list[str] = []
    for url in urls:
        masked = sanitize_url(url)
        if masked in seen:
            continue
        seen.add(masked)
        sanitized.append(masked)
    return sanitized


def load_recorded_urls(path: Path) -> list[str]:
    """从录制文件里取出 URL 列表（本工具 JSON / HAR / 任意嵌套的调试 JSON）。

    :param path: 录制文件路径
    :returns: URL 列表（文件缺失或解析失败时返回空列表）
    """
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(raw, dict):
        return harvest_urls(raw)

    # 本工具格式：{"requests": [{"url": ...}]}
    requests = raw.get("requests")
    if isinstance(requests, list):
        return [
            str(item.get("url", ""))
            for item in requests
            if isinstance(item, dict) and item.get("url")
        ]

    # HAR 格式：{"log": {"entries": [{"request": {"url": ...}}]}}
    log = raw.get("log")
    if isinstance(log, dict):
        entries = log.get("entries")
        if isinstance(entries, list):
            urls: list[str] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                request = entry.get("request")
                if isinstance(request, dict) and request.get("url"):
                    urls.append(str(request["url"]))
            return urls

    # 兜底：临时调试 json 没有约定形态（data/debug/*.json），递归抽
    return harvest_urls(raw)


#: 范围外调用：主机 → 原因（这些子系统不在刷课链路上，见 REFACTOR_PLAN §11 backlog）
IGNORED_HOSTS: dict[str, str] = {
    "mobilelearn.chaoxing.com": "移动端子系统（直播 / 签到 / 活动）",
    "robot-lc.chaoxing.com": "数字人助手接口",
    "robot-static.chaoxing.com": "数字人助手静态资源",
    "noteyd.chaoxing.com": "笔记插件",
    "stat2-ans.chaoxing.com": "前端埋点统计",
}

#: 范围外调用：路径后缀 → 原因（前端资源，不是 API 调用；注意**不含** .html / .json）
IGNORED_SUFFIXES: dict[str, str] = {
    ".js": "前端脚本",
    ".css": "前端样式",
    ".cssx": "前端样式（线上文件名拼作 cssx）",
    ".properties": "前端语言包",
    ".map": "source map",
    ".png": "图片资源",
    ".jpg": "图片资源",
    ".jpeg": "图片资源",
    ".gif": "图片资源",
    ".svg": "图片资源",
    ".woff": "字体资源",
    ".woff2": "字体资源",
    ".ttf": "字体资源",
}

#: 范围外调用：URL 前缀（scheme://host/path）→ 原因
IGNORED_PREFIXES: dict[str, str] = {
    "https://mooc2-ans.chaoxing.com/mooc2-ans/ai-evaluate/": "AI 评价活动（属新增功能范畴）",
}

#: 能力级缺口：任务点类型 / 链路未实现，无法用 URL 规则表达（渲染进「未还原原因」表）
UNMODELED_FACES: tuple[tuple[str, str], ...] = (
    (
        "discussion（讨论 / 回复任务点）",
        "类型已识别但执行器未实现（study._dispatch 对未知类型直接 skip）；无可用样本",
    ),
    (
        "exam（考试）",
        "端点已注册（exam_start / exam_submit），但无 client API 与执行器；交卷接口为 VOLATILE",
    ),
    ("人机验证（滑块 / 人脸）", "仅验证码图片接口已建模；滑块与人脸走人工介入（HumanRequired）"),
)


def is_ignored(url: str) -> bool:
    """该调用是否属于**已登记**的范围外调用（主机 → 前缀 → 后缀）。

    「范围外」不等于「看不见」：这些规则连同原因会渲染进
    ``docs/PROTOCOL_COVERAGE.md``，静默跳过才算漏账。

    :param url: 录制到的 URL
    :returns: 命中任一规则则为真
    """
    if (urlparse(url).hostname or "") in IGNORED_HOSTS:
        return True
    if any(url.startswith(prefix) for prefix in IGNORED_PREFIXES):
        return True
    path = urlparse(url).path
    return any(path.endswith(suffix) for suffix in IGNORED_SUFFIXES)


def unmatched_requests(recorded_urls: Sequence[str]) -> list[str]:
    """从录制到的请求里找出**没有建模**的学习通调用。

    :param recorded_urls: 录制到的完整 URL 列表
    :returns: 未匹配到任何端点的 URL（去重、保持顺序）
    """
    known_paths = {_endpoint_path(spec) for spec in REGISTRY.values()}
    unmatched: list[str] = []
    seen: set[str] = set()

    for url in recorded_urls:
        if not _is_chaoxing_url(url) or url in seen or is_ignored(url):
            continue
        seen.add(url)
        path = urlparse(url).path
        if any(_path_matches(path, known) for known in known_paths):
            continue
        unmatched.append(url)
    return unmatched


def _path_matches(path: str, template: str) -> bool:
    """把端点模板路径与实际请求路径做形状比对（``{cpi}`` 这类占位符按"任意一段"处理）。"""
    template_parts = [part for part in template.split("/") if part]
    path_parts = [part for part in path.split("/") if part]
    if len(template_parts) != len(path_parts):
        return False
    for expected, actual in zip(template_parts, path_parts, strict=True):
        if expected.startswith("{") and expected.endswith("}"):
            continue
        if expected != actual:
            return False
    return True


def summarise(rows: Sequence[CoverageRow]) -> dict[str, int]:
    """统计覆盖情况。

    :param rows: 覆盖记录
    :returns: 形如 ``{"total": 20, "verified": 10, "with_fixture": 4}``
    """
    return {
        "total": len(rows),
        "verified": sum(1 for row in rows if row.endpoint.calibration is Calibration.VERIFIED),
        "template": sum(1 for row in rows if row.endpoint.calibration is Calibration.TEMPLATE),
        "volatile": sum(1 for row in rows if row.endpoint.calibration is Calibration.VOLATILE),
        "with_fixture": sum(1 for row in rows if row.fixture),
    }


def build_rows(fixtures_dir: Path | None = None) -> list[CoverageRow]:
    """构造全部端点的覆盖记录。

    :param fixtures_dir: fixture 目录
    :returns: 覆盖记录列表（按注册顺序）
    """
    mapping = match_fixtures(REGISTRY.values(), fixtures_dir)
    return [
        CoverageRow(endpoint=spec, fixture=mapping.get(spec.name, "")) for spec in REGISTRY.values()
    ]


def build_markdown(
    rows: Sequence[CoverageRow],
    *,
    unmatched: Sequence[str] = (),
    recorded_count: int = 0,
) -> str:
    """生成 ``docs/PROTOCOL_COVERAGE.md`` 的内容。

    :param rows: 覆盖记录
    :param unmatched: 未建模的学习通请求
    :param recorded_count: 本次对账使用的录制请求条数
    :returns: markdown 文本
    """
    stats = summarise(rows)
    lines = [
        "# 协议覆盖清单（PROTOCOL_COVERAGE）",
        "",
        "> 由 `cx dev probe coverage` 自动生成，请勿手工编辑。",
        "> 目标（见 `docs/REFACTOR_PLAN.md` §5.14）：**把刷课链路上真实存在的包与 API call 还原齐全**，",
        "> 缺一条要写明原因，而不是默默略过。",
        "",
        "## 统计",
        "",
        f"- 已注册端点：**{stats['total']}**（VERIFIED {stats['verified']} / "
        f"TEMPLATE {stats['template']} / VOLATILE {stats['volatile']}）",
        f"- 已有测试样本：**{stats['with_fixture']}**",
        f"- 本次对账的录制请求：{recorded_count} 条，其中未建模 {len(unmatched)} 条",
        "",
        "## 端点明细",
        "",
        "| 端点 | 方法 | 路径 | 置信度 | 测试样本 | 说明 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        spec = row.endpoint
        note = spec.note.split("。")[0][:60]
        lines.append(
            f"| `{spec.name}` | {spec.method} | `{_endpoint_path(spec)}` | "
            f"{spec.calibration} | {row.fixture or '—'} | {note} |"
        )

    lines += ["", "## 未建模调用", ""]
    if unmatched:
        lines.append("以下学习通请求出现在真实录制里，但**尚无对应端点**（需要补建模或说明原因）：")
        lines.append("")
        lines += [f"- `{url}`" for url in unmatched]
    else:
        lines.append("（未发现）无对应端点的学习通请求。")

    lines += ["", "## 范围外调用（含原因）", ""]
    lines.append(
        "这些调用**不在刷课链路上**，但确实出现在录制里 —— 规则与原因集中在 "
        "`src/chaoxing/client/coverage.py`，改规则即改本表："
    )
    lines.append("")
    lines.append("| 规则 | 值 | 原因 |")
    lines.append("| --- | --- | --- |")
    lines += [f"| 主机 | `{host}` | {reason} |" for host, reason in IGNORED_HOSTS.items()]
    lines += [
        f"| 路径后缀 | `{suffix}` | {reason} |" for suffix, reason in IGNORED_SUFFIXES.items()
    ]
    lines += [
        f"| URL 前缀 | `{prefix}` | {reason} |" for prefix, reason in IGNORED_PREFIXES.items()
    ]

    lines += [
        "",
        "## 对账方式",
        "",
        "抓包由 **orca-cli** 驱动（本仓库不自建浏览器驱动）：**先开 HAR 再导航**，",
        "HAR 的落盘路径以 `network har stop` 的返回值为准（`start` 不接受路径）。",
        "",
        "```bash",
        "orca tab create --json",
        'orca exec --command "network har start" --json',
        "orca goto --url <课程页 URL> --json",
        'orca exec --command "network har stop" --json   # 记下 result.path',
        "cx dev capture sanitize --recorded <har 路径> "
        "--out tests/fixtures/recorded/course_session_urls.json",
        "cx dev probe coverage --recorded tests/fixtures/recorded/course_session_urls.json --write",
        "```",
        "",
        "（`cx dev capture browser` 是同一套 orca 流程的命令行封装；网页侧检查用 `scripts/web_check.sh`。）",
        "",
        "缺样本的端点用 `cx dev capture` 补录后重新生成本文件；",
        "无法还原的**能力级缺口**（不是单条调用）登记在下方：",
        "",
        "| 调用 / 能力 | 未还原原因 |",
        "| --- | --- |",
    ]
    lines += [f"| {face} | {reason} |" for face, reason in UNMODELED_FACES]
    lines.append("")
    return "\n".join(lines)
