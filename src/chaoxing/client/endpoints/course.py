"""课程与章节类端点。"""

from __future__ import annotations

from chaoxing.client.constants import DOMAIN_MOOC1, DOMAIN_MOOC1_1, DOMAIN_MOOC2_ANS
from chaoxing.client.endpoints.base import Calibration, Endpoint

COURSE_LIST = Endpoint(
    name="course_list",
    url=f"{DOMAIN_MOOC1}/visit/courselistdata",
    calibration=Calibration.VERIFIED,
    note=(
        "课程列表主来源。【实测】返回课程卡片 HTML（编码随版本变，早先是 GBK、现为 UTF-8 —— 一律按响应头判断，见 `decode_response`）；"
        "课程链接形如 /mooc-ans/visit/stucoursemiddle?courseid=..&clazzid=..&cpi=..&ismooc2=1"
    ),
    params={
        "courseType": "1",
        "courseFolderId": "0",
        "baseEducation": "0",
        "superstarClass": "0",
    },
)

COURSE_LIST_LEGACY = Endpoint(
    name="course_list_legacy",
    url=f"{DOMAIN_MOOC1_1}/visit/interaction",
    calibration=Calibration.TEMPLATE,
    note="老版课程列表 HTML（主来源失效时的兜底解析源）",
)

CHAPTER_CARDS = Endpoint(
    name="chapter_cards",
    url=f"{DOMAIN_MOOC1}/knowledge/cards",
    calibration=Calibration.TEMPLATE,
    required_params=("clazzid", "courseid", "knowledgeid"),
    note=(
        "章节任务点卡片（JSON）。视频心跳所需的 objectId / jobid / enc、"
        "文档所需的 jtoken **全部来自这里** —— 是整个任务点模块的前置依赖"
    ),
    params={
        "clazzid": "",
        "courseid": "",
        "knowledgeid": "",
        "num": "0",
        "ut": "s",
        "cpi": "",
        "isPhone": "0",
        "mobile": "0",
    },
)

COURSE_ENTRY = Endpoint(
    name="course_entry",
    url=f"{DOMAIN_MOOC1}/mooc-ans/visit/stucoursemiddle",
    calibration=Calibration.VERIFIED,
    required_params=("courseid", "clazzid", "cpi"),
    note=(
        "课程入口。【抓包实测】GET 后 302 到课程页 ``mooc2-ans/.../stu?...&enc={stuenc}&t=...``；"
        "**这里的 enc 就是后续章节页所需的 stuenc**，是打通测验/考试链路的关键一步"
    ),
    params={"courseid": "", "clazzid": "", "vc": "1", "cpi": "", "ismooc2": "1", "v": "2"},
)

CHAPTER_CONTENT = Endpoint(
    name="chapter_content",
    url=f"{DOMAIN_MOOC2_ANS}/mooc2-ans/mycourse/studentcourse",
    calibration=Calibration.VERIFIED,
    required_params=("courseid", "clazzid", "cpi", "stuenc"),
    note="章节内容页（含任务点卡片）；**必须带 stuenc 与 t**，否则服务端返回权限错误",
    params={"courseid": "", "clazzid": "", "cpi": "", "ut": "s", "t": "", "stuenc": ""},
)

CHAPTER_STUDY_PAGE = Endpoint(
    name="chapter_study_page",
    url=f"{DOMAIN_MOOC1}/mycourse/studentstudy",
    calibration=Calibration.TEMPLATE,
    note=(
        "真机录制命中（2026-10-11）。章节学习页（mooc1 域）—— 学生页入口，"
        "页面里再嵌 iframe 到 mooc2-ans 的章节页"
    ),
)

CHAPTER_STUDY_MOOC2 = Endpoint(
    name="chapter_study_mooc2",
    url=f"{DOMAIN_MOOC2_ANS}/mooc2-ans/mycourse/stu",
    calibration=Calibration.TEMPLATE,
    note="真机录制命中（2026-10-11）。新版章节页（mooc2-ans 域）；与 chapter_content 同域不同路径",
)

CHAPTER_TREE_AJAX = Endpoint(
    name="chapter_tree_ajax",
    url=f"{DOMAIN_MOOC1}/mooc-ans/mycourse/studentstudyAjax",
    calibration=Calibration.TEMPLATE,
    note="真机录制命中（2026-10-11）。章节树 / 目录展开的 Ajax",
)

CHAPTER_COURSE_LIST = Endpoint(
    name="chapter_course_list",
    url=f"{DOMAIN_MOOC1}/mooc-ans/mycourse/studentstudycourselist",
    calibration=Calibration.TEMPLATE,
    note="真机录制命中（2026-10-11）。章节页里的课程 / 章节列表",
)

CHAPTER_CARDS_MOOC = Endpoint(
    name="chapter_cards_mooc",
    url=f"{DOMAIN_MOOC1}/mooc-ans/knowledge/cards",
    calibration=Calibration.TEMPLATE,
    required_params=("clazzid", "courseid", "knowledgeid"),
    note=(
        "真机录制命中（2026-10-11）。卡片接口的 /mooc-ans 前缀变体。"
        "**与 chapter_cards 的关系已校准（2026-10-12 真机）**：两者返回的卡片数据一致，"
        "区别在**同一份响应里的两份载荷** —— HTML 卡的 data 属性（带类型信号、**没有 jtoken**）"
        "与内嵌脚本 JSON（如 mArg，**有 jtoken**）。此前只取 HTML 版，导致文档任务点带着空 jtoken "
        "落库（礼仪课 4/4 失败）；现由 parser 的 _script_payload/_merge_attachments 按身份键合并两份载荷"
    ),
)

COURSE_TRANSFER = Endpoint(
    name="course_transfer",
    url=f"{DOMAIN_MOOC1}/mycourse/transfer",
    calibration=Calibration.TEMPLATE,
    note="真机录制命中（2026-10-11）。课程跳转（导航）",
)
