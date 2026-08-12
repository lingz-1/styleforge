from __future__ import annotations

from styleforge.workflow.task_workflow import is_follow_up


def test_follow_up_detects_short_adjustments() -> None:
    assert is_follow_up("更正式一点")
    assert is_follow_up("太正式了")
    assert is_follow_up("再休闲点")
    assert is_follow_up("换件外套")
    assert is_follow_up("别那么花")


def test_follow_up_rejects_fresh_briefs() -> None:
    assert not is_follow_up("明天穿什么")
    assert not is_follow_up("帮我推荐一套")
    assert not is_follow_up("更正式的推荐")
    assert not is_follow_up("周末聚会穿什么")
    assert not is_follow_up("这次面试该怎么搭配")


def test_follow_up_rejects_long_or_plain_requests() -> None:
    assert not is_follow_up("")
    assert not is_follow_up("这件衣服可以搭配那条裤子吗")
    assert not is_follow_up("帮我把这套衣服换得正式一点并且适合通勤穿着")
