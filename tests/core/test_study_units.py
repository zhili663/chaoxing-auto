"""播放单元切分（纯函数）。

来源：legacy 的编排需求 —— "看完一个视频就先把紧随其后的题做完，再放下一个视频"。
这件事必须由**数据结构**保证，而不是靠调用顺序碰巧正确。
"""

from __future__ import annotations

from chaoxing.core.services.study import PointView, plan_units


def point(seq: int, kind: str, point_id: str = "") -> PointView:
    """造一个任务点视图。"""
    return PointView(id=seq, point_id=point_id or f"p{seq}", kind=kind, sequence=seq)


def test_video_starts_new_unit_and_followers_join() -> None:
    """视频开新单元，其后的题目并入当前单元。"""
    units = plan_units(
        [
            point(1001, "video"),
            point(1002, "quiz"),
            point(1003, "quiz"),
            point(1004, "video"),
            point(1005, "document"),
        ]
    )

    assert [unit.head.kind for unit in units] == ["video", "video"]
    assert [item.kind for item in units[0].followers] == ["quiz", "quiz"]
    assert [item.kind for item in units[1].followers] == ["document"]
    assert units[0].size == 3


def test_points_before_any_video_form_their_own_unit() -> None:
    """课程开头就是题（没有前置视频）时，它自成单元，不能丢。"""
    units = plan_units([point(1001, "quiz"), point(1002, "video")])

    assert len(units) == 2
    assert units[0].head.kind == "quiz"
    assert units[0].followers == []
    assert units[1].head.kind == "video"


def test_units_follow_sequence_order_regardless_of_input() -> None:
    """输入乱序也要按 sequence 排好（仓储虽然排序，但这里不该依赖调用方）。"""
    units = plan_units(
        [
            point(3000, "video"),
            point(1000, "quiz"),
            point(2000, "video"),
        ]
    )

    assert [unit.head.sequence for unit in units] == [1000, 2000, 3000]


def test_video_only_course_produces_one_unit_per_video() -> None:
    """纯视频课：每个视频一个单元。"""
    units = plan_units([point(index, "video") for index in (1, 2, 3)])

    assert len(units) == 3
    assert all(unit.followers == [] for unit in units)


def test_empty_input_gives_no_units() -> None:
    """没有任务点时不产生空单元。"""
    assert plan_units([]) == []


def test_point_without_sequence_still_planned() -> None:
    """sequence 缺失（默认 0）的任务点不能被吞掉。"""
    units = plan_units([PointView(id=1, point_id="p", kind="quiz")])

    assert len(units) == 1
    assert units[0].head.point_id == "p"
