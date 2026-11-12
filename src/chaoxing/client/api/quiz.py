"""测验域：换票 → 明文题 → 题目页 → 提交。

四道关卡（legacy 逐条踩过，缺一步就失败）：

1. 直接拼题目页 → ``[3004] 长时间没有操作或没有权限``，必须先经课程入口拿 **stuenc**；
2. 拿到 stuenc 后硬拼 ``doHomeWorkNew`` 仍 3004 → 必须用 ``quiz_redirect`` **换票**，
   服务端 302 到带数字 ``workId`` 与 ``enc`` 的真题目页；
3. 题干被 ``font-cxsecret`` 字体加密（HTML 里是随机码点）→ 用 ``quiz_plain_questions``
   取**明文题干**，与题目页按题目顺序一一对应；
4. 提交少一个参数就 ``无效的参数：code-1！`` → **9 个 URL 参数 + 5 个 body 字段**。
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

from chaoxing.client.api.base import ApiModule
from chaoxing.client.constants import DOMAIN_MOOC1, DOMAIN_MOOC1_API, XHR_HEADERS
from chaoxing.client.exceptions import ChaoxingError, HumanRequired
from chaoxing.client.model.base import Envelope, ensure_not_error_page, parse_envelope_strict
from chaoxing.client.session import SessionState

log = logging.getLogger(__name__)

#: 换票后的题目页在**不同版本**里对同一含义使用不同大小写：
#: 提交表单用 ``_classId`` / ``courseid``，而 JSON 接口用 ``classId`` / ``courseId``。
#: 校验码预检必须能同时认这些别名，否则参数为空 → 预检失败 → 交卷被拒。
_VERIFY_QUERY_ALIASES: dict[str, tuple[str, ...]] = {
    "courseId": ("courseId", "courseid"),
    "classId": ("classId", "clazzId", "classid", "clazzid"),
    "cpi": ("cpi",),
    "workId": ("workId", "workid"),
    "answerId": ("answerId", "workAnswerId", "answerid", "workanswerid"),
}


def _query_value(query: dict[str, str], key: str) -> str:
    """按别名从题目页查询串里取值（大小写与下划线差异都认）。"""
    for name in _VERIFY_QUERY_ALIASES[key]:
        value = query.get(name)
        if value:
            return str(value)
    return ""


def _xhr_get_headers(referer: str) -> dict[str, str]:
    """GET 版 XHR 头：浏览器 AJAX 不会给 GET 带 ``Content-Type``。

    直接套 :data:`XHR_HEADERS` 会给预检 GET 加一个表单 Content-Type ——
    形状不像浏览器，没必要冒这个风险。
    """
    headers = {key: value for key, value in XHR_HEADERS.items() if key != "Content-Type"}
    headers["Referer"] = referer
    return headers


#: 提交时 URL 上必须带的固定参数（实测口径）
SUBMIT_FIXED_QUERY: dict[str, str] = {
    "ua": "pc",
    "formType": "post",
    "saveStatus": "1",
    "value": "(NaN|NaN)",
    "version": "1",
}

#: body 里必须补的字段（页面隐藏字段之外的部分）
SUBMIT_EXTRA_BODY: dict[str, str] = {
    "workTimesEnc": "",
    "randomOptions": "false",
    "isAccessibleCustomFid": "0",
}


def random_pos() -> str:
    """提交参数 ``pos``：24 位随机十六进制。"""
    return secrets.token_hex(12)


def random_rd() -> str:
    """提交参数 ``rd``：随机小数。"""
    return f"{secrets.randbelow(10**12) / 10**12:.15f}"


def build_submit_query(work_id: str) -> dict[str, str]:
    """构造提交测验所需 URL 参数（缺任何一个都是"无效的参数：code-1！"）。

    :param work_id: 数字形式的作业 ID（注意**不是**卡片里那个 hex）
    :returns: 查询参数字典
    """
    query = dict(SUBMIT_FIXED_QUERY)
    query.update(
        {
            "pos": random_pos(),
            "rd": random_rd(),
            "wid": work_id,
            "_edt": str(int(time.time() * 1_000_000)),  # 微秒时间戳
        }
    )
    return query


def build_answerwqbid(question_ids: list[str]) -> str:
    """构造 ``answerwqbid``：逗号列表，**末尾也要有逗号**。

    :param question_ids: 题目 ID 列表
    :returns: 形如 ``"401151530,401151531,"``
    """
    return "".join(f"{qid}," for qid in question_ids)


#: 判断题取值归一化表（真机 2026-10-13：服务端只认 ``true`` / ``false``）
_JUDGE_TRUE = frozenset({"对", "正确", "√", "是", "1", "T", "TRUE", "Y", "YES"})
_JUDGE_FALSE = frozenset({"错", "错误", "×", "否", "0", "F", "FALSE", "N", "NO"})

#: 连线题作答的一对：左项序号 = 右项字母（``1=A`` / ``1:A`` / ``1：A`` 都认）
_MATCH_PAIR_PATTERN = re.compile(r"(\d+)\s*[=:：]\s*([A-Za-z])")


#: 页面 ``answertype`` → 期望的答案形状（真机实测 2026-10-16）：
#: 0=单选（单个字母）、1=多选（字母串）、3=判断题（true/false）。
#: 形状不符会被**整卷拒绝**（笼统回「作业提交失败！」），而不是只扣该题分 ——
#: 所以宁可留空，也绝不发错形。
_ANSWER_SHAPES: dict[str, re.Pattern[str]] = {
    "0": re.compile(r"[A-Z]"),
    "1": re.compile(r"[A-Z]+"),
    "3": re.compile(r"(true|false)", re.IGNORECASE),
}


def sanitize_answers_by_type(answers: dict[str, str], page_types: dict[str, str]) -> dict[str, str]:
    """按题目页的 ``answertype{id}`` 校验答案形状，丢掉形状不符的题。

    真机实测（2026-10-16，账号 2 课程 24）：8 个测验全部因**判断题位置发出字母**
    （明文题与题目页按索引合并后题型错位）被服务端整卷拒绝，返回笼统的
    「作业提交失败！」。只把那一题改成 ``true`` 后，同一份报文立刻 ``success!``。
    ⇒ 形状不符时**留空该题**：服务端接受留空（``#176`` 实测），不接受错形。

    :param answers: ``{"answer401151530": "ABC"}``
    :param page_types: ``{"401151530": "1", …}``（来自页面隐藏域 ``answertype{id}``）
    :returns: 过滤后的答案（顺序保持）
    """
    kept: dict[str, str] = {}
    for key, value in answers.items():
        question_id = key[len("answer") :]
        expected = page_types.get(question_id, "")
        pattern = _ANSWER_SHAPES.get(expected)
        if pattern is not None and not pattern.fullmatch(value.strip()):
            log.warning(
                "测验答案形状与页面题型不符，留空该题：%s=%r（answertype=%s）",
                question_id,
                value,
                expected,
            )
            continue
        kept[key] = value
    return kept


def build_match_answer(mapping: dict[str, str]) -> str:
    """把连线题的映射拼成服务端要的 JSON 数组。

    真机成功样本（2026-10-13，``#10`` 的同类题）：
    ``[{"name":"1","content":"D"},{"name":"2","content":"A"},…]``
    —— ``name`` 是左项 1-based 序号，``content`` 是右项字母；数组按序号升序、
    分隔符不留空格（``separators=(",", ":")``）。

    :param mapping: ``{左项序号: 右项字母}``
    :returns: JSON 字符串
    """
    items = [
        {"name": str(name), "content": str(content).strip().upper()}
        for name, content in sorted(mapping.items(), key=lambda item: int(item[0]))
    ]
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def parse_match_answer(value: str) -> dict[str, str]:
    """把模型给的 ``序号=字母`` 文本解析成映射（找不到任何一对时为空）。

    :param value: 模型作答（可多行，也容忍被压成一行的空白）
    :returns: ``{序号: 字母}``
    """
    return {name: letter.upper() for name, letter in _MATCH_PAIR_PATTERN.findall(value or "")}


def normalize_answer(qtype_hint: str, value: str) -> str | None:
    """把模型的作答规范化成服务端认的形式。

    - **判断题**：服务端要 ``true`` / ``false``（真机实测，发「对/错」整卷被拒）；
      认不出就返回 ``None`` —— 调用方**不许猜**，直接让这一卷失败（宁可不交，也别交错格式）。
    - **单选 / 多选**：去空白、转大写（``" a b c "`` → ``"ABC"``）。
    - **连线题**：解析 ``序号=字母`` 后拼成 JSON 数组（见 :func:`build_match_answer`）；
      一对都解析不出时返回 ``None``。
    - **填空题**与其它没确证格式的题型：返回 ``None`` —— **不许原样透传**（那等于把臆造文本发给服务端）。

    :param qtype_hint: 题型提示（如「判断题」，来自解析层）
    :param value: 模型作答
    :returns: 规范化后的值；无法规范化时为 ``None``
    """
    text = (value or "").strip()
    hint = (qtype_hint or "").strip()
    if hint == "判断题":
        if text in _JUDGE_TRUE or text.upper() in _JUDGE_TRUE:
            return "true"
        if text in _JUDGE_FALSE or text.upper() in _JUDGE_FALSE:
            return "false"
        return None
    if hint in ("单选题", "多选题"):
        return "".join(text.split()).upper() or None
    if "连线" in hint:
        mapping = parse_match_answer(text)
        return build_match_answer(mapping) if mapping else None
    if hint == "填空题":
        # 格式未确证（还没有成功样本）：返回 None，让调用方留空这一题
        return None
    return text or None


def submit_target(work_page_url: str) -> str:
    """推导提交地址：题目页**同级目录**下的 ``addStudentWorkNewWeb``。

    实测：提交不是打到题目页本身，而是打到它旁边的表单接收端点，
    并带上题目页 URL 里的 ``workId`` / ``enc`` 等参数。

    :param work_page_url: 换票得到的题目页地址
    :returns: 提交地址

    真机 App 的题目页在 ``/mooc-ans/work/phone/...`` 下，表单 action 是
    ``addStudentWorkNew``（没有 Web 后缀）；Web 题目页才用
    ``addStudentWorkNewWeb``。form1 存在时调用方优先用 action，本函数只作兜底。
    """
    if "/work/phone/" in work_page_url:
        # 真机 form1.action 是 /mooc-ans/work/addStudentWorkNew，不是 /work/phone/... 下的叶子。
        return urljoin(work_page_url, "/mooc-ans/work/addStudentWorkNew")
    return urljoin(work_page_url, "addStudentWorkNewWeb")


class QuizApi(ApiModule):
    """测验换票、取题与提交。"""

    async def redirect_to_work(
        self,
        session: SessionState,
        *,
        work_id: str,
        job_id: str,
        course_id: str,
        clazz_id: str,
        cpi: str = "",
        knowledge_id: str = "",
        ktoken: str = "",
        enc: str = "",
        utenc: str = "",
    ) -> str:
        """换票：把卡片里的作业标识换成真正可访问的题目页地址。

        :param enc: 卡片附件的 ``enc``（真机抓包：换票要带它，缺了服务端回 403）
        :param utenc: 课程页的 ``utEnc``（真机抓包：缺了它换票会被弹到登录页）
        :returns: 302 之后的题目页 URL（含数字 ``workId`` 与 ``enc`` 签名）
        """
        response = await self._get(
            session,
            "quiz_redirect",
            params={
                "workId": work_id,
                "jobid": job_id,
                "originJobId": job_id,
                "courseid": course_id,
                "clazzId": clazz_id,
                "cpi": cpi,
                "knowledgeid": knowledge_id,
                "ktoken": ktoken,
                "enc": enc,
                "utenc": utenc,
            },
            # ★ 必须按 **XHR 形状**发：换票属于经 ``/api/work`` 调的接口一族，
            #   真机抓包（2026-10-15）里浏览器对这类请求一律带 ``X-Requested-With`` +
            #   ``Origin`` + 卡片页 Referer —— 缺了它们服务端回 403 通用错误页。
            #   这条与 ``submit`` 的处置一致（见 ``client/constants.py`` 的 XHR_HEADERS 注释）。
            headers={
                **XHR_HEADERS,
                "Referer": f"{DOMAIN_MOOC1}/mooc-ans/knowledge/cards",
            },
        )
        final_url = str(response.url)
        # 合法落点不止 doHomeWork：已交卷的测验会落到批阅页
        # （``selectWorkQuestionYiPiYue``，真机 2026-10-16）；只对真正异常的形态告警，
        # 否则每次跑已完成的点都会刷一条误导性的 warning。
        if not any(marker in final_url for marker in ("doHomeWork", "selectWorkQuestion")):
            log.warning("换票后未落到题目页：%s", final_url)
        return final_url

    async def fetch_phone_work(
        self,
        session: SessionState,
        *,
        work_id: str,
        job_id: str,
        course_id: str,
        clazz_id: str,
        knowledge_id: str,
        cpi: str,
        ktoken: str = "",
        enc: str = "",
        user_id: str = "",
    ) -> tuple[str, str]:
        """打开手机 App 题目页（mworkspecial → work/phone/work）。

        这是 App 实际使用的链路；Web 的 ``api/work``/``doHomeWorkNew`` 是另一条。
        未交卷页会带 ``form1``，其 action 为 ``/mooc-ans/work/addStudentWorkNew``。

        :returns: ``(最终 URL, 题目页 HTML)``
        :raises ChaoxingError: 入口或题目页失败、落到错误页
        """
        response = await self._get(
            session,
            "quiz_phone_entry",
            params={
                "workid": work_id,
                "jobid": job_id,
                "originJobId": job_id,
                "needRedirect": "true",
                "courseid": course_id,
                "knowledgeid": knowledge_id,
                "userid": user_id,
                "ut": "s",
                "clazzId": clazz_id,
                "cpi": cpi,
                "ktoken": ktoken,
                "enc": enc,
            },
            headers={
                **XHR_HEADERS,
                "Referer": f"{DOMAIN_MOOC1_API}/ananas/modules/work/index_wap.html",
            },
        )
        final_url = str(response.url)
        text = response.text or ""
        ensure_not_error_page(text)
        return final_url, text

    async def fetch_plain_questions_html(
        self,
        session: SessionState,
        *,
        course_id: str,
        work_id: str,
        job_id: str,
        clazz_id: str = "",
        cpi: str = "",
        knowledge_id: str = "",
        ktoken: str = "",
    ) -> str:
        """取**明文题干**（绕过 ``font-cxsecret`` 字体反爬）。

        :returns: 明文题页面文本
        """
        response = await self._get(
            session,
            "quiz_plain_questions",
            params={
                "courseId": course_id,
                "workId": work_id,
                "jobid": job_id,
                "classId": clazz_id,
                "clazzId": clazz_id,
                "cpi": cpi,
                "knowledgeid": knowledge_id,
                "ktoken": ktoken,
            },
        )
        return response.text

    async def fetch_detail_html(self, session: SessionState, *, work_id: str, url: str = "") -> str:
        """取题目页 HTML（含 ``questionId`` 与提交所需的隐藏字段）。

        解析前先过错误页指纹 —— 题目页返回 3004 错误页时**绝不能**当成题目继续解析
        （legacy 实测把页脚文字解析成了一道题）。

        :param url: 换票得到的完整题目页地址；为空则按 ``work_id`` 拼默认地址
        :raises ErrorPageDetected: 命中 3004 等错误页指纹
        :returns: 题目页 HTML
        """
        target = url or self._transport_settings_work_url(work_id)
        response = await self._transport.request(
            session, target, headers={"Referer": "https://mooc1.chaoxing.com/"}
        )
        text = response.text
        ensure_not_error_page(text)
        return text

    def _transport_settings_work_url(self, work_id: str) -> str:
        """拼默认题目页地址（换票失败时的兜底，通常会被 3004 拦下）。"""
        from chaoxing.client import endpoints as ep

        return ep.get("quiz_detail").url + f"?workId={work_id}"

    async def fetch_verify_captcha(self, session: SessionState) -> bytes:
        """取作业验证码**图片**（答卷被要求人工验证码时用）。

        真机实测（2026-10-16）：某份答卷被服务端转入「要求人工验证码」态后，
        ``verify/code`` 会回「验证码不正确，请重新输入」；页面此时把图片指向
        ``/verifyCode/stuWork``（带时间戳防缓存）。必须人工读出四位码、带它重过
        ``verify/code`` 拿到 ``workTimesEnc``，再交卷 —— 这条链路就是"验证码接力"。

        :param session: 账号会话
        :returns: 图片字节（可直接写盘或喂给前端 ``<img>``）
        """
        from chaoxing.client import endpoints as ep

        response = await self._transport.request(
            session,
            f"{ep.get('quiz_verify_image').url}?{int(time.time() * 1000)}",
            headers={"Referer": f"{DOMAIN_MOOC1}/mooc-ans/work/doHomeWorkNew"},
        )
        return response.content

    async def _verify_code(
        self,
        session: SessionState,
        base_query: dict[str, str],
        input_code: str,
        *,
        referer: str = "",
    ) -> dict[str, object]:
        """过一次校验码预检，返回服务端原始 JSON（成功时 ``enc`` 即 workTimesEnc）。

        真机里这是题目页的 ``$.ajax``：带 ``X-Requested-With`` 与**本页 Referer**。
        题目页查询串的键名在不同版本里大小写不一（``courseid`` / ``courseId``），
        这里按别名取，避免预检因空参数直接失败、最后退化成「作业提交失败！」。
        """
        response = await self._get(
            session,
            "quiz_verify_code",
            params={
                "courseId": _query_value(base_query, "courseId"),
                "classId": _query_value(base_query, "classId"),
                "cpi": _query_value(base_query, "cpi"),
                "workId": _query_value(base_query, "workId"),
                "answerId": _query_value(base_query, "answerId"),
                "inputCode": input_code,
            },
            headers=_xhr_get_headers(referer or DOMAIN_MOOC1),
        )
        try:
            payload: dict[str, object] = response.json()
            return payload
        except ValueError:
            return {}

    async def submit(
        self,
        session: SessionState,
        *,
        work_id: str,
        url: str,
        answers: dict[str, str],
        hidden_fields: dict[str, str] | None = None,
        question_ids: list[str] | None = None,
        user_id: str = "",
        form_action: str = "",
        input_code: str = "",
    ) -> Envelope:
        """提交测验。

        URL 上带 9 个额外参数、body 里补齐 5 个字段（少一个就是"无效的参数：code-1！"）。

        :param work_id: 数字作业 ID
        :param url: 换票得到的题目页地址（提交就打到它上面）
        :param answers: ``{"answer401151530": "ABC"}`` 形式的答案
        :param hidden_fields: 页面隐藏字段（原样回传）
        :param question_ids: 题目 ID 列表，用于生成 ``answerwqbid``
        :param user_id: 当前用户 ID（body 必填项）
        :raises EnvelopeFailure: 服务端返回失败
        :returns: 解包后的信封
        """
        # 页面隐藏字段**优先**于我们的默认值：`randomOptions` 就是活例子 ——
        # 题目页写 `true`（选项每次渲染都乱序），我们曾用常量 `false` 覆盖它，
        # 真机实测（2026-10-13）浏览器发出去的正是页面的 `true`。
        body: dict[str, str] = dict(SUBMIT_EXTRA_BODY)
        body.update(hidden_fields or {})
        if user_id:
            body["userId"] = user_id
        if question_ids:
            body["answerwqbid"] = build_answerwqbid(question_ids)
        body.update(answers)

        # 提交地址 = 题目页同级目录的 addStudentWorkNewWeb；
        # 查询串 = 题目页原有参数（workId / enc …）+ 9 个必填参数。
        base_query = dict(parse_qsl(urlsplit(url).query))

        # ★ 交卷前必须过**校验码预检**（真机实测 2026-10-16）：题目页的 `form1submit()`
        #   会先 GET `/work/verify/code`，成功时把响应里的 `enc` 写进 `workTimesEnc`
        #   再真正提交。跳过这一步、body 里 `workTimesEnc` 为空 —— 服务端一律回
        #   「作业提交失败！」（与答案对错无关：换票/取题/作答全对也照样被拒）。
        try:
            verify_payload = await self._verify_code(session, base_query, input_code, referer=url)
        except ChaoxingError as exc:
            # 预检接口**不是所有账号/课程都可用**（真机 2026-10-16：账号 1 的
            # ``/mooc-ans/work/verify/code`` 整站 403，而同样的课文旧路径提交一直成功）。
            # 把它当硬失败会让老账号全线瘫痪 —— 只记一笔，按旧路径继续提交。
            log.warning("校验码预检不可用（%s），按旧路径直接提交", exc)
            verify_payload = {}
        if verify_payload and not verify_payload.get("status"):
            message = str(verify_payload.get("msg") or "")
            if "验证码" in message:
                # 服务端在这里说的是真话（例如「验证码不正确，请重新输入」= 该答卷已被要求
                # 人工验证码）；吞掉它继续提交只会得到与原因无关的「作业提交失败！」。
                raise HumanRequired(message or "校验码预检未通过")
            # 其它失败（服务繁忙 / 会话抖动）不是"要人读码"，降级走旧路径仍有成功机会。
            # 把它一律当人工会让一次暂时性故障卡住整份卷子。
            log.warning("校验码预检失败（%s），降级按旧路径提交", message or "未知原因")
            verify_payload = {}
        if verify_payload.get("status"):
            body["workTimesEnc"] = str(verify_payload.get("enc", ""))

        # ★ 提交目标 = 题目页表单自己的 action（真机实测 2026-10-16）：
        #   浏览器走 ``version(document.form1.action)``，即 action 原样 + ``version=1``。
        #   自己拼的那套查询串（api / enc / pos / rd / wid / _edt …）字段集虽然一样，
        #   但键名与取值都与表单不同 —— 服务端判定失败，统一回「作业提交失败！」。
        #   action 缺省时才回退到旧的拼接路径（保留给没有 form1 的页面结构）。
        if form_action:
            # action 可能是 ``addStudentWorkNewWeb?...``，也可能是 ``/mooc-ans/work/...``；
            # 用 urljoin 才能保留它自己的路径，而不是一律替换成 submit_target()。
            # 真机上 action 有时已经带 ``version=1``，此时不能再追加第二个。
            absolute = urljoin(url, form_action.replace("&amp;", "&"))
            parts = urlsplit(absolute)
            action_query = parts.query
            if "version=" not in action_query:
                action_query = f"{action_query}&version=1" if action_query else "version=1"
            target = urlunsplit(parts._replace(query=action_query))
            response = await self._transport.request(
                session,
                target,
                method="POST",
                data=body,
                headers={**XHR_HEADERS, "Referer": url},
            )
        else:
            query = {**base_query, **build_submit_query(work_id)}
            response = await self._transport.request(
                session,
                submit_target(url),
                method="POST",
                params=query,
                data=body,
                headers={**XHR_HEADERS, "Referer": url},
            )

        envelope = parse_envelope_strict(response.text)
        if not envelope.ok:
            # 服务端对「已转入验证码态」的答卷**有时只回通用的「作业提交失败！」**
            # （真机实测 2026-10-16：同一答卷时而「验证码不正确」、时而通用失败）。
            # 用空码再探一次 verify：它说验证码不对，就把原因归位到"需要人工"——
            # 否则调用方永远只看到一句与真因无关的失败。
            try:
                probe = await self._verify_code(session, base_query, "", referer=url)
            except ChaoxingError:
                # 预检不可用（账号 1 整站 403）时探针也拿不到信息 —— 保持原错误
                probe = {}
            if "验证码" in str(probe.get("msg", "")):
                raise HumanRequired(str(probe["msg"]))
        return envelope.require_ok()
