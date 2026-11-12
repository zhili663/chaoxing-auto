"""测验域：提交参数完整性与提交行为。

来源：legacy 抓包校准 —— 提交 URL 必须带 9 个额外参数、body 要补 5 个字段，
少一个就返回 ``无效的参数：code-1！``；``answerwqbid`` 是逗号列表且**末尾同样要逗号**。
"""

from __future__ import annotations

import re

import httpx
import pytest
import respx

from chaoxing.client import ChaoxingTransport, QuizApi, SessionState
from chaoxing.client.api.quiz import (
    build_answerwqbid,
    build_match_answer,
    build_submit_query,
    normalize_answer,
    sanitize_answers_by_type,
    submit_target,
)
from chaoxing.client.exceptions import HumanRequired
from chaoxing.config import ChaoxingSettings

MOOC1 = "https://mooc1.chaoxing.com"
WORK_URL = f"{MOOC1}/mooc-ans/work/doHomeWorkNew?workId=12345678&enc=abc"
SUBMIT_URL = f"{MOOC1}/mooc-ans/work/addStudentWorkNewWeb"


def _api() -> tuple[ChaoxingTransport, QuizApi]:
    transport = ChaoxingTransport(ChaoxingSettings(request_min_interval=0, max_retry=0))
    return transport, QuizApi(transport)


# --------------------------------------------------------------------------- #
# 参数构造（纯函数）
# --------------------------------------------------------------------------- #


def test_submit_target_uses_phone_endpoint_for_phone_work_page() -> None:
    """手机题目页的兜底提交端点是 addStudentWorkNew，不是 Web 后缀。"""
    phone = "https://mooc1-api.chaoxing.com/mooc-ans/work/phone/work?workId=1"
    web = f"{MOOC1}/mooc-ans/work/doHomeWorkNew?workId=1"

    assert submit_target(phone).endswith("/mooc-ans/work/addStudentWorkNew")
    assert submit_target(web).endswith("/work/addStudentWorkNewWeb")


def test_submit_query_has_all_nine_parameters() -> None:
    """9 个参数一个都不能少（少一个就是"无效的参数：code-1！"）。"""
    query = build_submit_query("12345678")

    assert set(query) == {
        "ua",
        "formType",
        "saveStatus",
        "pos",
        "rd",
        "value",
        "wid",
        "_edt",
        "version",
    }
    assert query["wid"] == "12345678"
    assert query["value"] == "(NaN|NaN)"


def test_pos_is_24_hex_and_changes_every_time() -> None:
    """``pos`` 是 24 位随机 hex（每次提交都要变，固定值会被判重放）。"""
    first = build_submit_query("1")["pos"]
    second = build_submit_query("1")["pos"]

    assert re.fullmatch(r"[0-9a-f]{24}", first)
    assert first != second


def test_edt_looks_like_microseconds() -> None:
    """``_edt`` 是微秒时间戳（16 位）。"""
    value = build_submit_query("1")["_edt"]

    assert value.isdigit()
    assert len(value) == 16


def test_answerwqbid_keeps_trailing_comma() -> None:
    """实测：末位也要逗号，去掉会被判参数非法。"""
    assert build_answerwqbid(["401151530", "401151531"]) == "401151530,401151531,"
    assert build_answerwqbid([]) == ""


def test_sanitize_answers_drops_shape_mismatch() -> None:
    """答案形状必须与页面 answertype 一致；错形会被**整卷**拒绝（真机 2026-10-16）。

    8 个测验全部因「判断题位置发出字母」被服务端整卷打回（笼统回「作业提交失败！」），
    只把那一题改成 `true` 后同一份报文立刻 `success!`。
    """
    answers = {
        "answer1": "A",  # answertype=0 单选 ✓
        "answer2": "AB",  # answertype=1 多选 ✓
        "answer3": "ABCD",  # answertype=3 判断 ✗ → 丢
        "answer4": "true",  # answertype=3 判断 ✓
        "answer5": "AC",  # answertype=2 填空（不约束）✓
    }
    types = {"1": "0", "2": "1", "3": "3", "4": "3", "5": "2"}

    kept = sanitize_answers_by_type(answers, types)

    assert kept == {"answer1": "A", "answer2": "AB", "answer4": "true", "answer5": "AC"}


def test_sanitize_answers_keeps_unknown_types() -> None:
    """页面没给题型（缺 answertype）时不动答案 —— 宁可原样，也别误删。"""
    answers = {"answer9": "ABCD"}

    assert sanitize_answers_by_type(answers, {}) == answers


# --------------------------------------------------------------------------- #
# 提交行为
# --------------------------------------------------------------------------- #


@respx.mock
async def test_submit_sends_query_body_and_answers() -> None:
    """提交：URL 带 9 参数、body 补齐必填字段与答案。"""
    respx.get(f"{MOOC1}/mooc-ans/work/verify/code").mock(
        return_value=httpx.Response(200, json={"enc": "e0bfc5fb", "status": True})
    )
    route = respx.post(SUBMIT_URL).mock(
        return_value=httpx.Response(200, json={"status": True, "msg": "success!"})
    )
    transport, api = _api()

    envelope = await api.submit(
        SessionState(account_id=1),
        work_id="12345678",
        url=WORK_URL,
        answers={"answer401151530": "ABC"},
        hidden_fields={"courseId": "265361351", "classId": "151148348"},
        question_ids=["401151530"],
        user_id="100000002",
    )

    assert envelope.ok is True
    request = next(call.request for call in respx.calls if call.request.method == "POST")
    assert "pos=" in str(request.url) and "version=1" in str(request.url)
    body = request.content.decode()
    assert "answer401151530=ABC" in body
    assert "answerwqbid=401151530%2C" in body
    assert "userId=100000002" in body
    # 预检拿到的 enc 必须原样进 body（真机：缺它一律「作业提交失败！」）
    assert "workTimesEnc=e0bfc5fb" in body
    assert route.called
    await transport.aclose()


@respx.mock
async def test_submit_surfaces_parameter_error() -> None:
    """服务端报"无效的参数"时必须抛错，不能当成提交成功。"""
    respx.get(f"{MOOC1}/mooc-ans/work/verify/code").mock(
        return_value=httpx.Response(200, json={"enc": "e0bfc5fb", "status": True})
    )
    respx.post(SUBMIT_URL).mock(
        return_value=httpx.Response(200, json={"status": False, "msg2": "无效的参数：code-1！"})
    )
    transport, api = _api()

    import pytest

    from chaoxing.client.exceptions import EnvelopeFailure

    with pytest.raises(EnvelopeFailure, match="无效的参数") as caught:
        await api.submit(SessionState(account_id=1), work_id="1", url=WORK_URL, answers={})
    # 异常必须带着服务端原始返回，供按账号日志记录“具体返回值”
    assert caught.value.payload["msg2"] == "无效的参数：code-1！"
    assert caught.value.payload["status"] is False
    await transport.aclose()


@respx.mock
async def test_submit_sends_xhr_headers() -> None:
    """平台按 XHR 校验提交：缺 `X-Requested-With` 就一律回「作业提交失败！」（真机 2026-10-13）。

    加头之前，7 个测验稳定被拒；加上这一组头后全部 `{"msg":"success!"}`。
    """
    respx.get(f"{MOOC1}/mooc-ans/work/verify/code").mock(
        return_value=httpx.Response(200, json={"enc": "e0bfc5fb", "status": True})
    )
    respx.post(SUBMIT_URL).mock(
        return_value=httpx.Response(200, json={"status": True, "msg": "success!"})
    )
    transport, api = _api()

    await api.submit(SessionState(account_id=1), work_id="12345678", url=WORK_URL, answers={})

    sent = next(call.request for call in respx.calls if call.request.method == "POST").headers
    assert sent["x-requested-with"] == "XMLHttpRequest"
    assert sent["accept"].startswith("application/json")
    assert sent["origin"] == MOOC1
    assert sent["content-type"] == "application/x-www-form-urlencoded; charset=UTF-8"
    await transport.aclose()


@pytest.mark.parametrize(
    ("hint", "raw", "expected"),
    [
        ("判断题", "对", "true"),
        ("判断题", "错", "false"),
        ("判断题", "√", "true"),
        ("判断题", "×", "false"),
        ("判断题", "T", "true"),
        ("判断题", "o", None),
        ("多选题", " a b c ", "ABC"),
        ("单选题", "d", "D"),
        (
            "连线题",
            "1=D\n2=A\n3=B\n4=C",
            '[{"name":"1","content":"D"},{"name":"2","content":"A"},'
            '{"name":"3","content":"B"},{"name":"4","content":"C"}]',
        ),
        ("连线题", "2=A 1=d", '[{"name":"1","content":"D"},{"name":"2","content":"A"}]'),
        ("连线题", "D", None),  # 没有「序号=字母」就不猜
        ("连线题", "", None),
    ],
)
def test_normalize_answer(hint: str, raw: str, expected: str | None) -> None:
    """判断题必须变成 true/false（真机：发「对/错」整卷被拒）；认不出时返回 None，让调用方别提交。"""
    assert normalize_answer(hint, raw) == expected


def test_build_match_answer_matches_success_sample() -> None:
    """连线题提交值严格等于真机成功样本的形状：无空格、按左项序号升序。"""
    assert (
        build_match_answer({"1": "D", "2": "A", "3": "B", "4": "C"})
        == '[{"name":"1","content":"D"},{"name":"2","content":"A"},'
        '{"name":"3","content":"B"},{"name":"4","content":"C"}]'
    )
    # 乱序输入 → 按序号升序；字母转大写
    assert (
        build_match_answer({"2": "a", "1": "d"})
        == '[{"name":"1","content":"D"},{"name":"2","content":"A"}]'
    )
    assert build_match_answer({}) == "[]"


# --------------------------------------------------------------------------- #
# 换票与取题
# --------------------------------------------------------------------------- #


@respx.mock
async def test_redirect_returns_final_work_url() -> None:
    """换票后的最终地址才是可访问的题目页（直接拼 doHomeWorkNew 会 3004）。"""
    respx.get(f"{MOOC1}/mooc-ans/api/work").mock(
        return_value=httpx.Response(302, headers={"location": WORK_URL})
    )
    respx.get(WORK_URL).mock(return_value=httpx.Response(200, text="<html>题目页</html>"))
    transport, api = _api()

    final_url = await api.redirect_to_work(
        SessionState(account_id=1),
        work_id="hexcardid",
        job_id="1601174638012256",
        course_id="265361351",
        clazz_id="151148348",
    )

    assert "doHomeWorkNew" in final_url
    await transport.aclose()


@respx.mock
async def test_detail_html_raises_on_error_page() -> None:
    """题目页返回 3004 错误页时必须中止 —— legacy 曾把页脚文字当成一道题提交。"""
    respx.get(WORK_URL).mock(
        return_value=httpx.Response(200, text="<html>您长时间没有操作 [3004]</html>")
    )
    transport, api = _api()

    import pytest

    from chaoxing.client.exceptions import ErrorPageDetected

    with pytest.raises(ErrorPageDetected):
        await api.fetch_detail_html(SessionState(account_id=1), work_id="1", url=WORK_URL)
    await transport.aclose()


# --------------------------------------------------------------------------- #
# 校验码预检与人工验证码
# --------------------------------------------------------------------------- #


@respx.mock
async def test_submit_falls_back_when_verify_is_blocked() -> None:
    """预检接口 403 时按旧路径直接提交。

    真机 2026-10-16：账号 1 的 ``/mooc-ans/work/verify/code`` **整站 403**，
    而同样课文用旧路径（无 ``workTimesEnc``）一直能成功 —— 把预检当硬失败会让
    老账号全线瘫痪，所以只记警告继续。
    """
    respx.get(f"{MOOC1}/mooc-ans/work/verify/code").mock(
        return_value=httpx.Response(403, text="<html>blocked</html>")
    )
    respx.post(SUBMIT_URL).mock(
        return_value=httpx.Response(200, json={"status": True, "msg": "success!"})
    )
    transport, api = _api()

    envelope = await api.submit(
        SessionState(account_id=1), work_id="1", url=WORK_URL, answers={"answer1": "A"}
    )

    assert envelope.ok is True
    await transport.aclose()


@respx.mock
async def test_submit_raises_human_required_when_verify_demands_captcha() -> None:
    """预检回「验证码不正确」时必须报"需要人工"，不许吞掉再提交。

    真机实测（2026-10-16）：答卷被服务端转入「要求人工验证码」态后，吞掉这句就会
    退化成与真因无关的「作业提交失败！」—— 排查方向被带偏。
    """
    respx.get(f"{MOOC1}/mooc-ans/work/verify/code").mock(
        return_value=httpx.Response(200, json={"msg": "验证码不正确，请重新输入", "status": False})
    )
    transport, api = _api()

    with pytest.raises(HumanRequired):
        await api.submit(
            SessionState(account_id=1),
            work_id="12345678",
            url=WORK_URL,
            answers={"answer1": "A"},
        )
    await transport.aclose()


@respx.mock
async def test_submit_probes_verify_when_submit_fails_generically() -> None:
    """提交只回通用「作业提交失败！」时，用空码再探一次 verify。

    真机实测（2026-10-16）：同一份答卷的服务端回答在这两句之间摆动 ——
    「验证码不正确，请重新输入」与「作业提交失败！」；不探一次就会把真因报丢。
    """
    respx.get(f"{MOOC1}/mooc-ans/work/verify/code").mock(
        side_effect=[
            httpx.Response(200, json={"enc": "e0bfc5fb93983650f5a53535ee72ba24", "status": True}),
            httpx.Response(200, json={"msg": "验证码不正确，请重新输入", "status": False}),
        ]
    )
    respx.post(SUBMIT_URL).mock(
        return_value=httpx.Response(200, json={"msg": "作业提交失败！", "status": False})
    )
    transport, api = _api()

    with pytest.raises(HumanRequired):
        await api.submit(
            SessionState(account_id=1),
            work_id="12345678",
            url=WORK_URL,
            answers={"answer1": "A"},
        )
    await transport.aclose()


@respx.mock
async def test_verify_code_uses_xhr_headers_and_lowercase_aliases() -> None:
    """预检是题目页里的 `$.ajax`：必须带 XHR 头与本页 Referer，且认小写查询键。

    换票落点不同版本会写 `courseid` / `classid` / `workid`；只读驼峰会让预检
    带着空参数发出去，最后退化成「作业提交失败！」。
    """
    verify = respx.get(f"{MOOC1}/mooc-ans/work/verify/code").mock(
        return_value=httpx.Response(200, json={"enc": "e0bfc5fb", "status": True})
    )
    respx.post(SUBMIT_URL).mock(
        return_value=httpx.Response(200, json={"status": True, "msg": "success!"})
    )
    transport, api = _api()
    lower_url = (
        f"{MOOC1}/mooc-ans/work/doHomeWorkNew?courseid=265361351&classid=151148348"
        "&cpi=100000001&workid=12345678&answerId=57041415&enc=abc"
    )

    await api.submit(SessionState(account_id=1), work_id="12345678", url=lower_url, answers={})

    request = verify.calls[0].request
    assert request.headers["x-requested-with"] == "XMLHttpRequest"
    assert request.headers["referer"] == lower_url
    assert "content-type" not in request.headers
    params = request.url.params
    assert params["courseId"] == "265361351"
    assert params["classId"] == "151148348"
    assert params["cpi"] == "100000001"
    assert params["workId"] == "12345678"
    assert params["answerId"] == "57041415"
    await transport.aclose()


@respx.mock
async def test_submit_uses_form_action_path_without_duplicate_version() -> None:
    """提交地址必须保留表单 action 自己的路径/查询，且不能追加第二个 version。

    action 在真机上既可能是相对文件名，也可能是 `/mooc-ans/work/...`；
    早先一律替换成 `submit_target(url)` 会丢掉 action 自己的路径。
    """
    form_action = (
        "/mooc-ans/work/addStudentWorkNewWeb?_classId=151148348&courseid=265361351"
        "&workid=12345678&token=enc-work&totalQuestionNum=20&version=1"
    )
    respx.get(f"{MOOC1}/mooc-ans/work/verify/code").mock(
        return_value=httpx.Response(200, json={"enc": "e0bfc5fb", "status": True})
    )
    submit = respx.post(url__startswith=f"{MOOC1}/mooc-ans/work/addStudentWorkNewWeb").mock(
        return_value=httpx.Response(200, json={"status": True, "msg": "success!"})
    )
    transport, api = _api()

    await api.submit(
        SessionState(account_id=1),
        work_id="12345678",
        url=WORK_URL,
        answers={"answer1": "A"},
        form_action=form_action,
    )

    request = submit.calls[0].request
    assert request.url.path == "/mooc-ans/work/addStudentWorkNewWeb"
    assert request.url.params["_classId"] == "151148348"
    assert request.url.params["token"] == "enc-work"
    assert request.url.params.get_list("version") == ["1"]
    await transport.aclose()


@respx.mock
async def test_verify_transient_failure_falls_back_to_submit() -> None:
    """预检回「服务繁忙」不是要人读码：降级走旧路径，仍有机会交卷成功。

    把所有 `status:false` 一律当人工验证码会让一次暂时性故障卡住整份卷子。
    """
    respx.get(f"{MOOC1}/mooc-ans/work/verify/code").mock(
        return_value=httpx.Response(200, json={"msg": "服务繁忙，请稍后再试", "status": False})
    )
    respx.post(SUBMIT_URL).mock(
        return_value=httpx.Response(200, json={"status": True, "msg": "success!"})
    )
    transport, api = _api()

    envelope = await api.submit(
        SessionState(account_id=1), work_id="1", url=WORK_URL, answers={"answer1": "A"}
    )

    assert envelope.ok is True
    await transport.aclose()
