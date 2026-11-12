"""测验 / 作业端点（四道关卡：会话上下文 → 换票 → 字体反爬 → 提交参数）。"""

from __future__ import annotations

from chaoxing.client.constants import DOMAIN_MOOC1, DOMAIN_MOOC1_API
from chaoxing.client.endpoints.base import Calibration, Endpoint

QUIZ_REDIRECT = Endpoint(
    name="quiz_redirect",
    url=f"{DOMAIN_MOOC1}/mooc-ans/api/work",
    calibration=Calibration.VERIFIED,
    required_params=("workId", "jobid"),
    note=(
        "测验换票接口。【抓包实测】GET 后 302 到真正的题目页 "
        "``/mooc-ans/work/doHomeWorkNew?workId={数字ID}&enc={签名}&...``。"
        "直接拼 doHomeWorkNew 会得到 [3004] 权限错误 —— 必须先走这里换票"
    ),
    params={
        "api": "1",
        "workId": "",
        "jobid": "",
        "originJobId": "",
        "needRedirect": "true",
        # 以下字段照真机抓包（2026-10-12）：大小写与"多出来的那几个"都不能省 ——
        # 我们此前只发 7 个参数，服务端一律回 403（同一个 URL 浏览器能进题目页）。
        "skipHeader": "true",
        "knowledgeid": "",
        "ktoken": "",
        "ut": "s",
        "clazzId": "",
        "type": "",
        "enc": "",
        "utenc": "",
        "courseid": "",
        "cpi": "",
        "mooc2": "1",
    },
)

QUIZ_PHONE_ENTRY = Endpoint(
    name="quiz_phone_entry",
    url=f"{DOMAIN_MOOC1_API}/mooc-ans/android/mworkspecial",
    calibration=Calibration.VERIFIED,
    required_params=("workid", "jobid", "courseid", "knowledgeid", "cpi"),
    note=(
        "手机 App 测验入口（真机抓包 2026-11-11）。GET 302 到 "
        "``/mooc-ans/work/phone/work``；Web 的 ``api/work`` 是另一条链路。"
        "必需参数含 workid（hex）、jobid、courseid、knowledgeid、clazzId、cpi、enc。"
    ),
    params={
        "workid": "",
        "jobid": "",
        "originJobId": "",
        "needRedirect": "true",
        "courseid": "",
        "knowledgeid": "",
        "userid": "",
        "ut": "s",
        "clazzId": "",
        "cpi": "",
        "ktoken": "",
        "enc": "",
    },
)

QUIZ_PHONE_WORK = Endpoint(
    name="quiz_phone_work",
    url=f"{DOMAIN_MOOC1_API}/mooc-ans/work/phone/work",
    calibration=Calibration.VERIFIED,
    required_params=("workId", "courseId", "clazzId", "jobId", "enc", "cpi"),
    note=(
        "手机 App 题目页（真机抓包 2026-11-11）。参数由 mworkspecial 302 透传："
        "workId/courseId/clazzId/knowledgeId/jobId/enc/cpi/originJobId。"
        "未交卷页面含 form1，action 为 /mooc-ans/work/addStudentWorkNew。"
    ),
    params={
        "workId": "",
        "courseId": "",
        "clazzId": "",
        "knowledgeId": "",
        "jobId": "",
        "enc": "",
        "cpi": "",
        "originJobId": "",
        "wMicroNodeId": "0",
    },
)

QUIZ_PHONE_REVIEW = Endpoint(
    name="quiz_phone_review",
    url=f"{DOMAIN_MOOC1_API}/mooc-ans/work/phone/selectWorkQuestionYiPiYue",
    calibration=Calibration.VERIFIED,
    note=(
        "手机 App 已批阅/已交卷题目页（真机抓包 2026-11-11）。"
        "查询参数含 courseId/workAnswerId/workId/knowledgeId/classId/oldWorkId/jobId/cpi。"
        "该页无 form1，不能用于提交；用于读取题目与答案形态。"
    ),
)

QUIZ_PHONE_SUBMIT = Endpoint(
    name="quiz_phone_submit",
    url=f"{DOMAIN_MOOC1_API}/mooc-ans/work/addStudentWorkNew",
    method="POST",
    calibration=Calibration.VERIFIED,
    note=(
        "手机 App 交卷接口（真机/H5 页面 form action，2026-11-11）。"
        "必须原样带 action 查询串 + body 隐藏字段 + answer{id} + answertype{id} + answerwqbid；"
        "不是 Web 的 addStudentWorkNewWeb。"
    ),
)

QUIZ_PLAIN_QUESTIONS = Endpoint(
    name="quiz_plain_questions",
    url=f"{DOMAIN_MOOC1}/mooc-ans/api/selectWorkQuestion",
    calibration=Calibration.VERIFIED,
    required_params=("courseId", "workId", "jobid"),
    note=(
        "明文题干接口（**绕过字体反爬的关键**）。题目页 doHomeWorkNew 的题干被 "
        "font-cxsecret 内嵌字体加密（HTML 里是随机码点，肉眼正常、复制出来是乱码），"
        "而本接口返回同样题目的未加密文本；两者按题目顺序一一对应 —— "
        "题干取这里，questionId 与提交字段取题目页"
    ),
    params={
        "api": "1",
        "courseId": "",
        "workId": "",
        "jobid": "",
        "originJobId": "",
        "knowledgeid": "",
        "classId": "",
        "clazzId": "",
        "cpi": "",
        "enc": "",
        "utenc": "",
        "ktoken": "",
        "mooc2": "1",
    },
)

QUIZ_DETAIL = Endpoint(
    name="quiz_detail",
    url=f"{DOMAIN_MOOC1}/mooc-ans/work/doHomeWorkNew",
    calibration=Calibration.TEMPLATE,
    required_params=("workId",),
    note=(
        "题目页（HTML，含题干/选项/隐藏字段）。参数由换票后的最终 URL 透传。"
        "**真机录制命中（2026-10-11）：路径带 /mooc-ans 前缀**，与换票 302 的落点一致"
        "（见 docs/MOOC_ANS_403.md §一）"
    ),
)

QUIZ_SUBMIT = Endpoint(
    name="quiz_submit",
    url=f"{DOMAIN_MOOC1}/mooc-ans/work/addStudentWorkNewWeb",
    method="POST",
    calibration=Calibration.VERIFIED,
    required_params=("workId",),
    note=(
        "提交测验。【抓包实测】URL 必须带 9 个额外参数 "
        "（ua / formType / saveStatus / pos / rd / value / wid / _edt / version），"
        "body 除页面隐藏字段外还要补 userId / workTimesEnc / randomOptions / "
        "isAccessibleCustomFid / answerwqbid，少一个就返回『无效的参数：code-1！』。"
        "答案字段名为 answer{questionId}，多选连写（answer401151530=ABC）。"
        "**真机录制命中（2026-10-11）：实录路径带 /mooc-ans 前缀**；"
        "实际提交地址由换票题目页同级目录推导（见 client/api/quiz.py 的 submit_target），本条目只作注册表记录"
    ),
)

QUIZ_WORK_PAGE = Endpoint(
    name="quiz_work_page",
    url=f"{DOMAIN_MOOC1}/ananas/modules/work/index.html",
    calibration=Calibration.TEMPLATE,
    note=(
        "真机录制命中（2026-10-11）。作业 / 测验模块页（iframe）—— "
        "与 video_player 同构，是测验链路的前置页面"
    ),
)

QUIZ_WORK_HANDLE = Endpoint(
    name="quiz_work_handle",
    url=f"{DOMAIN_MOOC1}/mooc-ans/workHandle/handle",
    calibration=Calibration.TEMPLATE,
    note=(
        "真机录制命中（2026-10-11）。换票 302 链的中间页"
        "（api/work → workHandle/handle → doHomeWorkNew）"
    ),
)

QUIZ_VALIDATE = Endpoint(
    name="quiz_validate",
    url=f"{DOMAIN_MOOC1}/mooc-ans/work/validate",
    calibration=Calibration.TEMPLATE,
    note="真机录制命中（2026-10-11）。交卷前的校验接口",
)

QUIZ_VERIFY_CODE = Endpoint(
    name="quiz_verify_code",
    url=f"{DOMAIN_MOOC1}/mooc-ans/work/verify/code",
    calibration=Calibration.VERIFIED,
    note=(
        "交卷前的校验码预检（真机实测 2026-10-16）：题目页 form1submit() 先 GET 这里，"
        "成功返回 {'enc': '…', 'status': true}，该 enc 即提交 body 的 workTimesEnc；"
        "缺这一步服务端一律回「作业提交失败！」。"
        "★ 早期注册的 /verifyCode/stuWork 与服务端实际调用不符（已实测纠正）"
    ),
)

QUIZ_VERIFY_IMAGE = Endpoint(
    name="quiz_verify_image",
    url=f"{DOMAIN_MOOC1}/verifyCode/stuWork",
    calibration=Calibration.VERIFIED,
    note=(
        "作业验证码**图片**（真机实测 2026-10-16）：页面 workVerifyCode() 把它的 src "
        "指向这里并带时间戳防缓存。答卷被服务端置入「要求人工验证码」态后，"
        "verify/code 会回「验证码不正确，请重新输入」—— 此时必须取这张图、"
        "人工读出四位码，再用 inputCode 重新过 verify/code"
    ),
)
