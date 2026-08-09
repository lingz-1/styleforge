from __future__ import annotations

import pytest

from styleforge.orchestration.graph import MultiTaskGraph
from styleforge.orchestration.task_router import TaskRouter, TaskType


@pytest.mark.parametrize(
    ("user_query", "expected"),
    [
        ("明天去纽约上班穿什么？", TaskType.OUTFIT_RECOMMEND),
        ("今天下雨，帮我搭一套舒服的衣服", TaskType.OUTFIT_RECOMMEND),
        ("鞋不好看，换一双。", TaskType.OUTFIT_MODIFY),
        ("保留其他单品，只换掉外套", TaskType.OUTFIT_MODIFY),
        ("American Vintage 风格应该怎么穿？", TaskType.STYLE_ADVICE),
        ("美拉德风格有什么特点？", TaskType.STYLE_ADVICE),
        ("Cowboy Boots 怎么搭？", TaskType.ITEM_ADVICE),
        ("黑色马甲怎么搭？", TaskType.ITEM_ADVICE),
        ("这件风衣如何搭配？", TaskType.ITEM_ADVICE),
        ("这件新大衣和我的衣柜搭吗？", TaskType.WARDROBE_COMPATIBILITY),
        ("这双鞋值得买吗？", TaskType.WARDROBE_COMPATIBILITY),
        ("我的衣柜还缺什么？", TaskType.WARDROBE_GAP),
        ("要搭配中世纪风格，我的衣柜还缺什么？", TaskType.WARDROBE_GAP),
        ("分析一下衣橱缺口", TaskType.WARDROBE_GAP),
        ("How should I dress for a client meeting tomorrow?", TaskType.OUTFIT_RECOMMEND),
        ("Replace the shoes but keep everything else.", TaskType.OUTFIT_MODIFY),
        ("How do I wear gorpcore?", TaskType.STYLE_ADVICE),
        ("How to style cowboy boots?", TaskType.ITEM_ADVICE),
        (
            "Is this trench coat compatible with my wardrobe?",
            TaskType.WARDROBE_COMPATIBILITY,
        ),
        ("What is missing from my wardrobe?", TaskType.WARDROBE_GAP),
    ],
)
def test_task_router_classifies_representative_requests(
    user_query: str,
    expected: TaskType,
) -> None:
    assert TaskRouter().route(user_query).task_type is expected


def test_structured_context_has_priority_over_text_fallback() -> None:
    router = TaskRouter()

    assert router.route("重新处理一下", current_outfit_id="outfit-1").task_type is TaskType.OUTFIT_MODIFY
    compatibility_route = router.route("帮我看看", has_candidate_item=True)
    assert compatibility_route.task_type is TaskType.WARDROBE_COMPATIBILITY


def test_explicit_task_type_is_validated_and_preserved() -> None:
    route = TaskRouter().route(
        "给我一些建议",
        requested_task_type=TaskType.STYLE_ADVICE,
    )

    assert route.task_type is TaskType.STYLE_ADVICE
    assert route.confidence == 1.0
    assert route.reason == "explicit_task_type"


def test_modify_route_extracts_target_slot() -> None:
    route = TaskRouter().route("鞋不好看，换一双")

    assert route.extracted == {"target_slot": "footwear"}


@pytest.mark.parametrize("task_type", list(TaskType))
def test_multitask_graph_reaches_the_matching_subgraph(task_type: TaskType) -> None:
    payload = MultiTaskGraph().route(
        user_id="demo-user",
        request="测试显式任务路由",
        requested_task_type=task_type,
    )

    assert payload["task_type"] == task_type.value
    assert payload["selected_subgraph"] == f"{task_type.value}_subgraph"
    assert payload["status"] == "routed"
    assert payload["required_capabilities"]
    assert [event["node"] for event in payload["trace"]] == [
        "task_router",
        f"{task_type.value}_subgraph",
    ]
