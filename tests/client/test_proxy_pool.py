"""代理池的解析、粘性与冷却（`chaoxing.client.proxy`）。

对应实测背景：跑批数小时后账号被 ``mooc-ans/*`` 403，换出口 IP 即可恢复 ——
所以池子要能把各种"一行一条"的导入文本吃下去，并且**同账号粘性、被拒才换**。
"""

from chaoxing.client.proxy import ProxyPool, parse_line


# --------------------------------------------------------------------------- #
# 导入解析
# --------------------------------------------------------------------------- #
def test_parses_four_common_line_formats() -> None:
    """供应商给的写法五花八门，这四种都必须认。"""
    cases = {
        "1.2.3.4:8080": "http://1.2.3.4:8080",
        "1.2.3.4:8080:alice:sec ret": "http://alice:sec ret@1.2.3.4:8080",
        "alice:secret@1.2.3.4:8080": "http://alice:secret@1.2.3.4:8080",
        "http://alice:secret@1.2.3.4:8080": "http://alice:secret@1.2.3.4:8080",
    }
    for raw, expected in cases.items():
        entry = parse_line(raw)
        assert entry is not None, raw
        assert entry.url == expected, raw


def test_keeps_scheme_and_hides_credentials_in_label() -> None:
    """socks5 要保留协议；label 不能泄露口令（日志会打它）。"""
    entry = parse_line("socks5://bob:hunter2@10.0.0.9:1080")
    assert entry is not None
    assert entry.url == "socks5://bob:hunter2@10.0.0.9:1080"
    assert entry.label == "10.0.0.9:1080"
    assert "hunter2" not in entry.label


def test_ignores_blanks_comments_and_garbage() -> None:
    """空行、注释、无法识别的行都不能让导入失败。"""
    text = "\n# 注释\n1.2.3.4:8080\n\n这不是代理\n5.6.7.8:3128  # 行尾注释\n"
    pool = ProxyPool.from_text(text)
    assert [item.url for item in pool.entries] == ["http://1.2.3.4:8080", "http://5.6.7.8:3128"]
    assert parse_line("这不是代理") is None
    assert parse_line("") is None


def test_deduplicates_same_endpoint() -> None:
    """同一个出口写两遍只保留一条。"""
    pool = ProxyPool.from_text("1.2.3.4:8080\n1.2.3.4:8080\n5.6.7.8:8080")
    assert len(pool.entries) == 2


# --------------------------------------------------------------------------- #
# 挑选、粘性与冷却
# --------------------------------------------------------------------------- #
def test_empty_pool_means_direct_connection() -> None:
    """没有代理就返回 None —— 调用方直连，行为与从前完全一致。"""
    pool = ProxyPool()
    assert pool.enabled is False
    assert pool.pick("1") is None


def test_same_key_sticks_to_same_exit() -> None:
    """同账号粘性：连续取多次都得到同一个出口（不主动轮换，换 IP 本身是风控信号）。"""
    pool = ProxyPool.from_text("1.2.3.4:8080\n5.6.7.8:8080")

    first = pool.pick("1")
    assert first is not None
    for _ in range(5):
        assert pool.pick("1") is first

    # 另一个账号走另一个出口（跨账号不共享出口）
    other = pool.pick("2")
    assert other is not None and other is not first


def test_rotate_unbinds_and_switches_without_cooling() -> None:
    """主动轮换：旧出口不冷却，只解绑；下次选最久未用的出口。"""
    pool = ProxyPool.from_text("1.2.3.4:8080\n5.6.7.8:8080")
    first = pool.pick("1")
    assert first is not None

    second = pool.rotate("1")

    assert second is not None and second is not first
    assert first.cooling is False
    assert first.fail_count == 0
    assert pool.pick("1") is second


def test_blocked_exit_goes_cooling_and_key_switches() -> None:
    """命中 403 的那条被冷却，同一个账号**立刻换到另一个出口**。"""
    pool = ProxyPool.from_text("1.2.3.4:8080\n5.6.7.8:8080", cooldown_s=60)
    first = pool.pick("1")
    assert first is not None

    pool.mark_blocked(first, reason="403")

    assert first.cooling is True
    assert first.fail_count == 1
    second = pool.pick("1")
    assert second is not None
    assert second is not first  # 换出口了
    assert second.status == "ok"


def test_all_cooling_falls_back_to_direct() -> None:
    """全部出口都在冷却时返回 None（直连兜底），不能让跑批直接卡死。"""
    pool = ProxyPool.from_text("1.2.3.4:8080", cooldown_s=60)
    entry = pool.pick("1")
    assert entry is not None
    pool.mark_blocked(entry)

    assert pool.pick("1") is None
    assert pool.pick("2") is None


def test_stats_never_leaks_credentials() -> None:
    """统计输出只给 label/计数/冷却剩余，绝不带出口 URL。"""
    pool = ProxyPool.from_text("http://alice:secret@1.2.3.4:8080")
    entry = pool.pick("1")
    assert entry is not None
    pool.mark_ok(entry)

    rows = pool.stats()
    assert rows and rows[0]["label"] == "1.2.3.4:8080"
    assert rows[0]["ok"] == 1
    assert "secret" not in str(rows)


def test_sync_remote_preserves_stickiness_and_health() -> None:
    """远端快照替换列表时，同 URL 的粘性与成功统计不能丢（否则账号会无故换 IP）。"""
    pool = ProxyPool.from_text("1.2.3.4:8080\n5.6.7.8:8080")
    first = pool.pick("1")
    assert first is not None
    pool.mark_ok(first)

    pool.sync_remote([parse_line("http://1.2.3.4:8080"), parse_line("http://9.9.9.9:8080")])  # type: ignore[list-item]

    bound = pool.pick("1")
    assert bound is not None
    assert bound.url == "http://1.2.3.4:8080"
    assert bound.ok_count == 1
    assert {entry.url for entry in pool.entries} == {
        "http://1.2.3.4:8080",
        "http://9.9.9.9:8080",
    }


def test_sync_remote_keeps_cooldown_and_unbinds_removed() -> None:
    """冷却中的坏出口不能被快照“洗白”；远端已删除的出口必须解绑。"""
    pool = ProxyPool.from_text("1.2.3.4:8080\n5.6.7.8:8080", cooldown_s=60)
    bad = pool.pick("1")
    assert bad is not None
    pool.mark_blocked(bad)

    pool.sync_remote([parse_line("http://1.2.3.4:8080")])  # type: ignore[list-item]

    kept = next(entry for entry in pool.entries if entry.url == "http://1.2.3.4:8080")
    assert kept.cooling is True
    assert pool.pick("1") is None, "唯一出口仍在冷却时只能直连"

    pool.sync_remote([parse_line("http://5.6.7.8:8080")])  # type: ignore[list-item]
    assert pool.pick("1") is not None
