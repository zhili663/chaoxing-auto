"""会话状态的回归：cookie 合并、令牌更新、快照往返。"""

from __future__ import annotations

import httpx

from chaoxing.client import SessionState


def _response(cookies: dict[str, str]) -> httpx.Response:
    """造一个带 Set-Cookie 的响应。"""
    return httpx.Response(
        200,
        headers=[("set-cookie", f"{name}={value}; Path=/") for name, value in cookies.items()],
        request=httpx.Request("GET", "https://mooc1.chaoxing.com/x"),
    )


def test_cookie_header_joins_pairs() -> None:
    """Cookie 头必须是标准的 ``k=v; k=v`` 形式。"""
    session = SessionState(account_id=1, cookies={"_uid": "42", "fid": "3568"})

    header = session.cookie_header()

    assert header == "_uid=42; fid=3568"


def test_absorb_updates_and_removes_cookies() -> None:
    """空值 Set-Cookie 代表删除（学习通登出时会这么干）。"""
    session = SessionState(account_id=1, cookies={"_uid": "42", "old": "1"})

    changed = session.absorb(_response({"_uid": "99", "old": ""}))

    assert session.cookies == {"_uid": "99"}
    assert changed == 2
    assert session.dirty is True


def test_absorb_without_change_keeps_session_clean() -> None:
    """同样的 cookie 不该把会话标记成脏（否则节流持久化就失效了）。"""
    session = SessionState(account_id=1, cookies={"_uid": "42"})

    changed = session.absorb(_response({"_uid": "42"}))

    assert changed == 0
    assert session.dirty is False


def test_tokens_ignore_empty_values() -> None:
    """空令牌不得覆盖已有值（卡片里常见空字段，覆盖了心跳就 403）。"""
    session = SessionState(account_id=1, tokens={"dtoken": "keep"})

    session.update_tokens(dtoken="", enc="new")

    assert session.token("dtoken") == "keep"
    assert session.token("enc") == "new"


def test_snapshot_round_trip_preserves_credentials() -> None:
    """快照往返：cookie 与令牌必须原样回来（P2 落库要靠它）。"""
    session = SessionState(
        account_id=7,
        username="13800000000",
        cookies={"_uid": "42"},
        tokens={"enc": "abc"},
        referer="https://mooc1.chaoxing.com/x",
    )

    restored = SessionState.from_snapshot(session.snapshot())

    assert restored.account_id == 7
    assert restored.username == "13800000000"
    assert restored.cookies == {"_uid": "42"}
    assert restored.tokens == {"enc": "abc"}
    assert restored.dirty is False  # 刚恢复的会话没有未落库变更


def test_authenticated_requires_status_and_cookies() -> None:
    """ "已登录"的最小特征：状态 + 至少一个 cookie。"""
    assert SessionState(account_id=1, status="logged_in", cookies={"_uid": "1"}).is_authenticated
    assert not SessionState(account_id=1, status="logged_in").is_authenticated
    assert not SessionState(account_id=1, cookies={"_uid": "1"}).is_authenticated
