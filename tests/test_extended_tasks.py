from __future__ import annotations

from pathlib import Path

import pytest

from styleforge.llm.client import LlmUnavailable
from styleforge.models.agent_tasks import Agent1TaskOutput
from styleforge.models.task import CandidateItem, TaskExecutionInput
from styleforge.orchestration.task_router import TaskRouter, TaskType
from styleforge.repositories.catalog_repository import upsert_items
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.task_run_repository import get_task_run
from styleforge.repositories.wardrobe_repository import add_items
from styleforge.tools.extension_analysis import analyze_extension_task
from styleforge.tools.extension_validation import _validate_flexible
from styleforge.workflow.task_workflow import MultiTaskWorkflow

from tests.extension_llm import ScriptedExtensionLlm, approved_review, intent_response
from tests.helpers import make_item


KNOWLEDGE_ROOT = Path("knowledge")


def test_recommendation_weather_is_copied_into_shared_context_pack(
    db_dsn: str,
) -> None:
    database_path = _seed_database(db_dsn)
    weather = {
        "status": "available",
        "source": "open-meteo",
        "requested_location": "上海",
        "forecast_date": "2026-08-11",
        "temperature_min_c": 25,
        "temperature_max_c": 32,
    }
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=KNOWLEDGE_ROOT,
        llm_client=None,
        recommendation_runner=lambda **_: {
            "structured_result": {"status": "completed", "recommendations": []},
            "environment_context": {"weather": weather},
        },
    )

    payload = workflow.execute(
        TaskExecutionInput(
            user_id="u",
            request="明天在上海参加户外活动，帮我推荐穿搭",
            requested_task_type=TaskType.OUTFIT_RECOMMEND,
        )
    )

    assert payload["context_pack"]["environment_context"]["weather"] == weather
    assert payload["result"]["environment_context"]["weather"] == weather


def test_task_execution_forwards_device_location_context(db_dsn: str) -> None:
    database_path = _seed_database(db_dsn)
    captured: dict[str, object] = {}
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=KNOWLEDGE_ROOT,
        llm_client=None,
        recommendation_runner=lambda **kwargs: captured.update(kwargs) or {
            "structured_result": {"status": "completed", "recommendations": []},
            "environment_context": {},
        },
    )
    location_context = {
        "latitude": 31.234567,
        "longitude": 121.474444,
        "accuracy_m": 85.0,
        "captured_at": "2026-08-10T08:00:00+00:00",
        "source": "device",
        "consent_granted": True,
    }

    workflow.execute(
        TaskExecutionInput(
            user_id="u",
            request="今晚在上海的露台约会穿什么",
            requested_task_type=TaskType.OUTFIT_RECOMMEND,
            location_context=location_context,
        )
    )

    # The device location flows verbatim into the recommendation subgraph.
    assert captured.get("location_context") == location_context
    assert captured.get("user_id") == "u"
    assert captured.get("request") == "今晚在上海的露台约会穿什么"


def _seed_database(db_dsn: str, user_id: str = "u") -> str:
    database_path = db_dsn
    initialize_database(database_path)
    items = [
        make_item("vest", "top", "White tailored waistcoat vest", "white"),
        make_item("shirt", "top", "White collared shirt", "white"),
        make_item("tee", "top", "Vintage graphic t-shirt", "cream"),
        make_item("knit", "top", "Brown knit sweater", "brown"),
        make_item("trousers", "pants", "Black tailored trousers", "black"),
        make_item("jeans", "pants", "Blue straight jeans", "blue"),
        make_item("skirt", "skirt", "Gray pleated skirt", "gray"),
        make_item("loafers", "shoes", "Black leather loafers", "black"),
        make_item("sneakers", "shoes", "White running sneakers", "white"),
        make_item("boots", "shoes", "Brown ankle boots", "brown"),
        make_item("trench", "outwear", "Beige trench coat", "beige"),
        make_item("leather", "outwear", "Black leather jacket", "black"),
        make_item("bag", "bag", "Brown tote bag", "brown"),
    ]
    with database_session(database_path) as connection:
        upsert_items(connection, items, "test")
        add_items(connection, user_id, [item.item_id for item in items])
    return database_path


def _workflow(
    database_path: str,
    agent2_response: dict,
) -> tuple[MultiTaskWorkflow, ScriptedExtensionLlm]:
    llm = ScriptedExtensionLlm(
        [
            intent_response("理解请求并建立衣橱事实范围"),
            agent2_response,
            approved_review(),
            # Each successful execute also runs one memory-extraction call.
            {"evidence": []},
        ]
    )
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=KNOWLEDGE_ROOT,
        llm_client=llm,
        recommendation_runner=lambda **_: {
            "structured_result": {"status": "completed", "recommendations": []}
        },
    )
    return workflow, llm


def _item_advice_draft(*, summary: str = "以黑色马甲组合衣橱单品") -> dict:
    return {
        "task_type": "item_advice",
        "status": "completed",
        "summary": summary,
        "result": {
            "status": "completed",
            "knowledge_type": "item",
            "title": "黑色马甲搭配",
            "summary": summary,
            "anchor_item": {"item_id": "candidate-preview", "name": "black vest"},
            "anchor_source": "candidate",
            "compatible_items_by_slot": {
                "top": [{"item_id": "shirt", "name": "White collared shirt"}],
                "bottom": [{"item_id": "trousers", "name": "Black tailored trousers"}],
                "footwear": [{"item_id": "loafers", "name": "Black leather loafers"}],
            },
            "wardrobe_matches": [],
            "wardrobe_matches_by_slot": {},
            "sample_outfits": [
                {
                    "outfit_id": "vest-look-1",
                    "item_ids": ["candidate-preview", "shirt", "trousers", "loafers"],
                    "reasoning": "白衬衫作为内层，黑裤和乐福鞋保持利落。",
                }
            ],
            "evidence": [{"source_id": "item-vest"}],
            "clarification_question": "",
            "limitations": [],
        },
        "used_item_ids": ["shirt", "trousers", "loafers"],
        "evidence_source_ids": ["item-vest"],
    }


def _assert_three_agent_execution(payload: dict, llm: ScriptedExtensionLlm) -> None:
    assert payload["llm_enabled"] is True
    assert payload["llm_call_count"] == 3
    assert len(llm.calls) == 4  # 3 agent calls + 1 memory-extraction call
    assert [item["node"] for item in payload["trace"]][-3:] == [
        "semantic_retriever_agent",
        "composer_agent",
        "critic_agent",
    ]
    assert all(not step.get("degraded", False) for step in payload["trace"][-3:])


def test_local_modification_locks_non_target_items(db_dsn: str) -> None:
    database_path = _seed_database(db_dsn)
    agent2 = {
        "task_type": "outfit_modify",
        "status": "completed",
        "summary": "只替换鞋履",
        "result": {
            "status": "completed",
            "current_outfit_id": "",
            "target_slot": "footwear",
            "replaced_item_ids": ["loafers"],
            "locked_item_ids": ["shirt", "trousers", "trench"],
            "alternatives": [
                {
                    "outfit_id": "modified-1",
                    "item_ids": ["shirt", "trousers", "trench", "sneakers"],
                    "score": 88.0,
                    "reasoning": "保留上衣、裤装和外套，只换成运动鞋。",
                },
                {
                    "outfit_id": "modified-2",
                    "item_ids": ["shirt", "trousers", "trench", "boots"],
                    "score": 84.0,
                    "reasoning": "保留上衣、裤装和外套，换成棕色短靴，通勤也够正式。",
                },
                {
                    "outfit_id": "modified-3",
                    "item_ids": ["shirt", "trousers", "leather", "sneakers"],
                    "score": 81.0,
                    "reasoning": "只换鞋履不够时，也可以把风衣换成黑色皮衣，脚下改运动鞋更放松。",
                },
            ],
            "message": "已锁定其他三件单品，只替换鞋履。",
        },
        "used_item_ids": ["shirt", "trousers", "trench", "sneakers"],
        "evidence_source_ids": [],
    }
    workflow, llm = _workflow(database_path, agent2)
    payload = workflow.execute(
        TaskExecutionInput(
            user_id="u",
            request="鞋太正式，只换一双，其他保持不变。",
            requested_task_type=TaskType.OUTFIT_MODIFY,
            current_item_ids=["shirt", "trousers", "loafers", "trench"],
            target_slot="footwear",
        )
    )

    assert payload["result"]["locked_item_ids"] == ["shirt", "trousers", "trench"]
    assert payload["result"]["alternatives"][0]["item_ids"] == [
        "shirt",
        "trousers",
        "trench",
        "sneakers",
    ]
    _assert_three_agent_execution(payload, llm)


def test_modify_missing_target_reports_unsatisfiable_instead_of_clarifying(
    db_dsn: str,
) -> None:
    database_path = _seed_database(db_dsn)
    router = TaskRouter()
    request = "把外套换成白色短西装的"
    route = router.route(request)
    assert route.task_type is TaskType.OUTFIT_MODIFY
    task_input = TaskExecutionInput(
        user_id="u",
        request=request,
        # Current outfit has no outerwear: shirt + trousers + loafers only.
        current_item_ids=["shirt", "trousers", "loafers"],
    )
    output = analyze_extension_task(
        database_path=database_path,
        knowledge_root=KNOWLEDGE_ROOT,
        task_input=task_input,
        route=route,
        context_pack=None,  # _analyze_modify discards it; not touched for OUTFIT_MODIFY
    )

    # A missing replacement target is no longer a dead-end clarification nor a
    # free rebuild: the change set drives a structured retrieval whose
    # feasibility report states the fact (the wardrobe has no suit) instead of
    # silently substituting any outerwear.  Relaxation/asking is Agent 2's
    # decision (PR3/PR4), not a fact-layer one.
    assert output.task_type is TaskType.OUTFIT_MODIFY
    assert output.needs_clarification is False
    assert output.facts.get("adjustment_mode") == "structured"
    report = output.facts.get("feasibility_report")
    assert report is not None
    assert report["state"] == "UNSATISFIABLE"
    assert report["unmet_constraints"] == ["西装"]
    # The pool still gives a slot-balanced fallback with named cards; current
    # items are never candidates.
    texts = output.facts.get("candidate_item_texts")
    assert texts
    by_id = {card["item_id"]: card for card in texts}
    assert not (set(by_id) & {"shirt", "trousers", "loafers"})
    current_texts = output.facts.get("current_item_texts")
    assert {card["item_id"] for card in current_texts} == {
        "shirt", "trousers", "loafers",
    }


def test_flexible_named_candidates_expose_hat_for_concrete_request(
    db_dsn: str,
) -> None:
    """When the user names a concrete item type (a hat), the flexible candidate
    pool must surface that type's items by name so Agent 2 can pick a hat rather
    than any accessory (regression for "加帽子却给戒指")."""
    database_path = _seed_database(db_dsn)
    # Seed a hat so the wardrobe actually contains one.
    hat = make_item("cap", "hats", "Logo cotton baseball cap", "navy")
    with database_session(database_path) as connection:
        upsert_items(connection, [hat], "test")
        add_items(connection, "u", [hat.item_id])

    router = TaskRouter()
    request = "再休闲一点，搭一个帽子"
    # The session router redirects a follow-up like this to OUTFIT_MODIFY; mirror
    # that here so the analyze path under test is the modification branch.
    route = router.route(request, requested_task_type=TaskType.OUTFIT_MODIFY)
    assert route.task_type is TaskType.OUTFIT_MODIFY
    task_input = TaskExecutionInput(
        user_id="u",
        request=request,
        # Current outfit has no accessory: shirt + trousers + loafers only.
        current_item_ids=["shirt", "trousers", "loafers"],
    )
    output = analyze_extension_task(
        database_path=database_path,
        knowledge_root=KNOWLEDGE_ROOT,
        task_input=task_input,
        route=route,
        context_pack=None,
    )

    assert output.facts.get("adjustment_mode") == "flexible"
    assert output.facts.get("required_slot") == "accessory"
    required_ids = output.facts.get("required_slot_item_ids")
    assert required_ids and "cap" in required_ids
    # The hat must be resolvable to a named card (type + name), not a bare UUID.
    texts = {card["item_id"]: card for card in output.facts.get("candidate_item_texts")}
    assert texts["cap"]["item_type"] == "hats"
    assert "baseball cap" in texts["cap"]["name"]


def test_flexible_adjust_accepts_llm_choice_of_replaced_and_locked(db_dsn: str) -> None:
    """Free-form rebuilds may replace/lock any subset and reference the full
    candidate pool without hard-failing (regression for EXT-001, where an
    overall tweak crashed because the replaced/locked sets were forced to equal
    Agent 1's empty sets)."""
    database_path = _seed_database(db_dsn)
    agent2 = {
        "task_type": "outfit_modify",
        "status": "completed",
        "summary": "整体调整得更正式",
        "result": {
            "status": "completed",
            "current_outfit_id": "",
            "target_slot": "",
            "replaced_item_ids": ["shirt"],
            "locked_item_ids": ["trousers", "trench"],
            "alternatives": [
                {
                    "outfit_id": "formal-1",
                    "item_ids": ["vest", "trousers", "trench", "boots"],
                    "score": 90.0,
                    "reasoning": "保留裤装与外套，换成白马甲和皮鞋，整体更正式。",
                },
                {
                    "outfit_id": "smart-1",
                    "item_ids": ["knit", "skirt", "trench", "loafers"],
                    "score": 85.0,
                    "reasoning": "毛衣与半身裙搭配皮靴与风衣，稳重知性。",
                },
                {
                    "outfit_id": "casual-1",
                    "item_ids": ["tee", "jeans", "leather", "sneakers"],
                    "score": 80.0,
                    "reasoning": "T 恤牛仔裤配皮夹克与运动鞋，休闲街头。",
                },
            ],
            "message": "整体更正式。",
        },
        "used_item_ids": [
            "vest", "trousers", "trench", "boots",
            "knit", "skirt", "loafers", "tee", "jeans", "leather", "sneakers",
        ],
        "evidence_source_ids": [],
    }
    workflow, llm = _workflow(database_path, agent2)
    payload = workflow.execute(
        TaskExecutionInput(
            user_id="u",
            request="整体再正式一点",
            requested_task_type=TaskType.OUTFIT_MODIFY,
            current_item_ids=["shirt", "trousers", "loafers", "trench"],
        )
    )

    assert payload["status"] == "completed"
    assert payload["result"]["alternatives"][0]["item_ids"] == [
        "vest",
        "trousers",
        "trench",
        "boots",
    ]
    _assert_three_agent_execution(payload, llm)


def test_validate_flexible_rejects_duplicate_core_slots_and_too_few_alternatives() -> None:
    """A rebuilt outfit must not carry two pairs of shoes (or any duplicated core
    slot) and flexible mode must offer at least 3 alternatives."""
    agent1 = Agent1TaskOutput(
        task_type=TaskType.OUTFIT_MODIFY,
        intent_summary="整体调整",
        facts={
            "current_item_ids": ["top1", "pant1", "shoe1"],
            "candidate_item_types": {
                "top1": "top", "pant1": "pants", "shoe1": "shoes",
                "shoe2": "shoes", "top2": "top",
            },
            "current_item_texts": [],
            "required_slot": "",
        },
        candidate_item_ids=["top1", "pant1", "shoe1", "shoe2", "top2"],
    )
    result = {
        "alternatives": [
            # Two shoes in one outfit -> rejected on slot duplication.
            {"item_ids": ["top1", "pant1", "shoe1", "shoe2"]},
            # A valid single variant — but only 2 alternatives total.
            {"item_ids": ["top2", "pant1", "shoe1"]},
        ]
    }
    issues = _validate_flexible(agent1, result)
    assert any("重复核心槽位" in issue for issue in issues)
    assert any("至少需要 3 套" in issue for issue in issues)


def test_validate_flexible_allows_stackable_types_but_not_non_stackable() -> None:
    """Layered tops and stacked jewellery are legal; two hats or two bags are
    not. Core slots stay one-per-outfit even across sub-types (pants+skirt)."""
    agent1 = Agent1TaskOutput(
        task_type=TaskType.OUTFIT_MODIFY,
        intent_summary="整体调整",
        facts={
            "current_item_ids": [],
            "candidate_item_types": {
                "shirt": "top", "cardigan": "top",
                "ear1": "earrings", "ring1": "rings", "neck1": "necklace",
                "brace1": "bracelet",
                "hat1": "hats", "hat2": "hats",
                "bag1": "bag", "bag2": "bag",
                "pant1": "pants", "skirt1": "skirt",
            },
            "current_item_texts": [],
            "required_slot": "",
        },
        candidate_item_ids=["shirt", "cardigan", "ear1", "ring1", "neck1",
                            "brace1", "hat1", "hat2", "bag1", "bag2",
                            "pant1", "skirt1"],
    )

    def issues_for(item_ids: list[str]) -> list[str]:
        return _validate_flexible(
            agent1, {"alternatives": [{"item_ids": item_ids} for _ in range(3)]}
        )

    # Layered tops (shirt + cardigan) pass.
    layered = issues_for(["shirt", "cardigan"])
    assert not any("重复" in issue for issue in layered)
    # Stacked jewellery (earrings + rings + necklace + bracelet) passes.
    stacked = issues_for(["ear1", "ring1", "neck1", "brace1"])
    assert not any("重复" in issue for issue in stacked)
    # Two hats -> rejected on the item type.
    two_hats = issues_for(["hat1", "hat2"])
    assert any("重复单品类型" in issue for issue in two_hats)
    # Two bags -> rejected on the item type.
    two_bags = issues_for(["bag1", "bag2"])
    assert any("重复单品类型" in issue for issue in two_bags)
    # Pants + skirt together -> rejected on the core slot (sub-type union).
    pants_and_skirt = issues_for(["pant1", "skirt1"])
    assert any("重复核心槽位" in issue for issue in pants_and_skirt)


def test_agent2_system_states_slot_uniqueness_only_for_flexible() -> None:
    """The hard dressing rule lives at system level so the LLM follows it up
    front, but only for flexible rebuilds — other task types keep the generic
    system prompt (e.g. item advice may legitimately reference a bag + shoes)."""
    from styleforge.llm.extension_prompts import build_extension_agent2_prompt
    from styleforge.models.context import ContextPack, RequestContext, UserContext

    context = ContextPack(
        request_context=RequestContext(
            original_request="请求",
            task_type="outfit_modify",
            route_reason="test",
            route_confidence=1.0,
        ),
        user_context=UserContext(user_id="u"),
    )

    flexible = Agent1TaskOutput(
        task_type=TaskType.OUTFIT_MODIFY,
        intent_summary="整体调整",
        facts={"adjustment_mode": "flexible"},
        candidate_item_ids=[],
    )
    system, _ = build_extension_agent2_prompt(
        request="年轻一点", context_pack=context, agent1_output=flexible
    )
    assert "硬性搭配约束" in system
    assert "核心槽位" in system

    local_modify = Agent1TaskOutput(
        task_type=TaskType.OUTFIT_MODIFY,
        intent_summary="只替换下装",
        facts={"adjustment_mode": ""},
        candidate_item_ids=[],
    )
    system, _ = build_extension_agent2_prompt(
        request="换条裤子", context_pack=context, agent1_output=local_modify
    )
    assert "硬性搭配约束" not in system

    # Non-outfit tasks must not inherit the outfit-scoped slot rule.
    item_advice = Agent1TaskOutput(
        task_type=TaskType.ITEM_ADVICE,
        intent_summary="单品建议",
        facts={},
        candidate_item_ids=[],
    )
    system, _ = build_extension_agent2_prompt(
        request="白衬衫怎么搭", context_pack=context, agent1_output=item_advice
    )
    assert "硬性搭配约束" not in system


def test_sanitize_prunes_duplicate_flexible_slots_and_types() -> None:
    """A flexible draft whose LLM packed two bottoms / two bags into one
    alternative must be pruned by ``sanitize_extension_references`` so the hard
    validator accepts it instead of failing the whole task run."""
    from styleforge.models.agent_tasks import Agent2TaskOutput
    from styleforge.tools.extension_validation import (
        sanitize_extension_references,
        validate_extension_draft,
    )

    agent1 = Agent1TaskOutput(
        task_type=TaskType.OUTFIT_MODIFY,
        intent_summary="整体调整",
        facts={
            "adjustment_mode": "flexible",
            "current_item_ids": [],
            "candidate_item_types": {
                "top1": "top", "pant1": "pants", "pant2": "pants",
                "bag1": "bag", "bag2": "bag", "shoe1": "shoes",
            },
            "current_item_texts": [],
            "required_slot": "",
        },
        candidate_item_ids=[
            "top1", "pant1", "pant2", "bag1", "bag2", "shoe1",
        ],
    )
    draft = Agent2TaskOutput(
        task_type=TaskType.OUTFIT_MODIFY,
        status="completed",
        summary="年轻一点",
        result={
            "alternatives": [
                # Two pants (same bottom slot) + two bags in one alternative.
                {"item_ids": ["top1", "pant1", "pant2", "bag1", "bag2", "shoe1"]},
                {"item_ids": ["top1", "pant1", "bag1", "shoe1"]},
                {"item_ids": ["top1", "pant1", "bag2", "shoe1"]},
            ]
        },
        used_item_ids=["top1", "pant1", "pant2", "bag1", "bag2", "shoe1"],
    )
    cleaned = sanitize_extension_references(agent1, draft)
    assert cleaned.result["alternatives"][0]["item_ids"] == [
        "top1", "pant1", "bag1", "shoe1",
    ]
    # The pruned draft passes the same validator that used to hard-fail.
    validate_extension_draft(agent1=agent1, agent2=cleaned, wardrobe_ids=set(
        agent1.candidate_item_ids
    ))


def test_sanitize_keeps_stackable_layering_and_accessories() -> None:
    """Pruning must never strip layered tops or stacked jewellery — those are
    legal repeats that the flexible rule explicitly allows."""
    from styleforge.models.agent_tasks import Agent2TaskOutput
    from styleforge.tools.extension_validation import sanitize_extension_references

    agent1 = Agent1TaskOutput(
        task_type=TaskType.OUTFIT_MODIFY,
        intent_summary="整体调整",
        facts={
            "adjustment_mode": "flexible",
            "current_item_ids": [],
            "candidate_item_types": {
                "shirt": "top", "cardigan": "top",
                "ear1": "earrings", "ring1": "rings",
                "pant1": "pants", "shoe1": "shoes",
            },
            "current_item_texts": [],
            "required_slot": "",
        },
        candidate_item_ids=[
            "shirt", "cardigan", "ear1", "ring1", "pant1", "shoe1",
        ],
    )
    draft = Agent2TaskOutput(
        task_type=TaskType.OUTFIT_MODIFY,
        status="completed",
        summary="叠穿",
        result={
            "alternatives": [
                {"item_ids": ["shirt", "cardigan", "ear1", "ring1", "pant1", "shoe1"]},
                {"item_ids": ["shirt", "cardigan", "ear1", "pant1", "shoe1"]},
                {"item_ids": ["shirt", "ear1", "ring1", "pant1", "shoe1"]},
            ]
        },
        used_item_ids=["shirt", "cardigan", "ear1", "ring1", "pant1", "shoe1"],
    )
    cleaned = sanitize_extension_references(agent1, draft)
    assert cleaned.result["alternatives"][0]["item_ids"] == [
        "shirt", "cardigan", "ear1", "ring1", "pant1", "shoe1",
    ]


def test_flexible_candidate_pool_is_bounded_and_slot_balanced() -> None:
    """The flexible rebuild pool must stay bounded (a full wardrobe of UUIDs
    blows the Agent 2 prompt context) and keep every core slot represented so
    the composer can rebuild a complete outfit (regression for the two-shoes /
    one-alternative fix, where candidate_item_ids was the entire wardrobe)."""
    from styleforge.tools.extension_analysis import _bounded_balanced_candidates

    # 300 items across five core slots + accessories, all outside the outfit.
    wardrobe = [
        make_item(f"top-{i}", "top", f"Top {i}")
        for i in range(60)
    ]
    wardrobe += [
        make_item(f"pant-{i}", "pants", f"Pant {i}")
        for i in range(60)
    ]
    wardrobe += [
        make_item(f"shoe-{i}", "shoes", f"Shoe {i}")
        for i in range(60)
    ]
    wardrobe += [
        make_item(f"coat-{i}", "outwear", f"Coat {i}")
        for i in range(60)
    ]
    wardrobe += [
        make_item(f"dress-{i}", "dress", f"Dress {i}")
        for i in range(60)
    ]
    current = ["top-0", "pant-0", "shoe-0", "coat-0"]

    pool = _bounded_balanced_candidates(wardrobe, current, priority_ids=[])
    assert len(pool) <= 120
    assert len(pool) == len(set(pool))
    assert set(current).isdisjoint(pool)
    # Every core slot survives the sampling (top / bottom / footwear / outerwear
    # / one_piece), so a rebuild never starves a slot.
    slots = {item.item_id: item.item_type for item in wardrobe}
    covered = {slots[uid] for uid in pool}
    assert {"top", "pants", "shoes", "outwear", "dress"} <= covered

    # Priority items (required-slot / direction-matched) come first, bounded.
    pool2 = _bounded_balanced_candidates(
        wardrobe, current, priority_ids=["top-5", "top-6", "top-7"]
    )
    assert pool2[:3] == ["top-5", "top-6", "top-7"]


def test_style_advice_uses_local_knowledge_as_supporting_evidence(db_dsn: str) -> None:
    database_path = _seed_database(db_dsn)
    agent2 = {
        "task_type": "style_advice",
        "status": "completed",
        "summary": "用衣橱已有单品落实美式复古",
        "result": {
            "status": "completed",
            "knowledge_type": "style",
            "title": "American Vintage 美式复古",
            "summary": "以图案 T 恤、牛仔裤和皮夹克建立复古层次。",
            "principles": [{"title": "层次", "content": "控制年代元素数量。"}],
            "wardrobe_matches": [{"item_id": "tee", "name": "Vintage graphic t-shirt"}],
            "evidence": [{"source_id": "style-american-vintage"}],
            "limitations": [],
        },
        "used_item_ids": ["tee"],
        "evidence_source_ids": ["style-american-vintage"],
    }
    workflow, llm = _workflow(database_path, agent2)
    payload = workflow.execute(
        TaskExecutionInput(user_id="u", request="American Vintage 风格应该怎么穿？")
    )

    assert payload["task_type"] == "style_advice"
    assert payload["result"]["evidence"][0]["source_id"] == "style-american-vintage"
    assert payload["result"]["wardrobe_matches"][0]["item_id"] == "tee"
    _assert_three_agent_execution(payload, llm)


def test_item_advice_anchors_wardrobe_item_and_builds_outfit(db_dsn: str) -> None:
    database_path = _seed_database(db_dsn)
    agent2 = {
        "task_type": "item_advice",
        "status": "completed",
        "summary": "以黑色马甲为锚点组合衣橱单品",
        "result": {
            "status": "completed",
            "knowledge_type": "item",
            "title": "黑色马甲搭配",
            "summary": "用白衬衫建立内层，以黑裤和乐福鞋保持利落。",
            "anchor_item": {"item_id": "candidate-preview", "name": "black vest"},
            "anchor_source": "candidate",
            "compatible_items_by_slot": {
                "top": [{"item_id": "shirt", "name": "White collared shirt"}],
                "bottom": [{"item_id": "trousers", "name": "Black tailored trousers"}],
                "footwear": [{"item_id": "loafers", "name": "Black leather loafers"}],
            },
            "wardrobe_matches": [],
            "wardrobe_matches_by_slot": {},
            "sample_outfits": [
                {
                    "outfit_id": "vest-look-1",
                    "item_ids": ["candidate-preview", "shirt", "trousers", "loafers"],
                    "reasoning": "锚点马甲与利落下装、鞋履形成完整搭配。",
                }
            ],
            "evidence": [{"source_id": "item-vest"}],
            "clarification_question": "",
            "limitations": [],
        },
        "used_item_ids": ["shirt", "trousers", "loafers"],
        "evidence_source_ids": ["item-vest"],
    }
    workflow, llm = _workflow(database_path, agent2)
    payload = workflow.execute(TaskExecutionInput(user_id="u", request="黑色马甲怎么搭？"))

    assert payload["task_type"] == "item_advice"
    assert payload["result"]["anchor_item"]["item_id"] == "candidate-preview"
    assert "candidate-preview" in payload["result"]["sample_outfits"][0]["item_ids"]
    assert "shirt" in payload["agent_outputs"]["agent1"]["candidate_item_ids"]
    _assert_three_agent_execution(payload, llm)


def test_compatibility_keeps_candidate_transient(db_dsn: str) -> None:
    database_path = _seed_database(db_dsn)
    agent2 = {
        "task_type": "wardrobe_compatibility",
        "status": "completed",
        "summary": "候选牛仔靴能与衣橱基础单品组成完整搭配",
        "result": {
            "status": "completed",
            "candidate_item": {"item_id": "candidate-preview", "name": "Brown cowboy boots"},
            "candidate_slot": "footwear",
            "compatibility_score": 86.0,
            "recommendation": "recommended",
            "recommendation_text": "衣橱已有上衣和下装能支持多套搭配。",
            "compatible_item_counts": {"top": 1, "bottom": 1},
            "compatible_items_by_slot": {
                "top": [{"item_id": "shirt", "name": "White collared shirt"}],
                "bottom": [{"item_id": "jeans", "name": "Blue straight jeans"}],
            },
            "complete_outfit_count": 1,
            "sample_outfits": [
                {
                    "outfit_id": "candidate-look-1",
                    "wardrobe_item_ids": ["shirt", "jeans"],
                    "reasoning": "白衬衫和牛仔裤能与候选靴履形成完整日常造型。",
                }
            ],
            "redundancy_score": 30.0,
            "similar_wardrobe_items": [{"item_id": "boots", "name": "Brown ankle boots"}],
            "evidence": [{"source_id": "item-cowboy-boots"}],
            "clarification_question": "",
            "limitations": [],
        },
        "used_item_ids": ["shirt", "jeans", "boots"],
        "evidence_source_ids": ["item-cowboy-boots"],
    }
    workflow, llm = _workflow(database_path, agent2)
    payload = workflow.execute(
        TaskExecutionInput(
            user_id="u",
            request="这双牛仔靴值得买吗？",
            candidate_item=CandidateItem(
                name="Brown cowboy boots",
                item_type="shoes",
                subtype="boots",
                color="brown",
            ),
        )
    )

    assert payload["result"]["complete_outfit_count"] == 1
    with database_session(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM catalog_items WHERE item_id = 'candidate-preview'"
        ).fetchone()[0] == 0
    _assert_three_agent_execution(payload, llm)


def test_targeted_medieval_gap_and_task_run_persistence(db_dsn: str) -> None:
    database_path = _seed_database(db_dsn)
    agent2 = {
        "task_type": "wardrobe_gap",
        "status": "completed",
        "summary": "衣橱已覆盖马甲和靴履，缺少长线条裙装",
        "result": {
            "status": "completed",
            "analysis_mode": "targeted",
            "target": {"style": "Medieval-Inspired 中世纪灵感风格"},
            "wardrobe_item_count": 13,
            "slot_counts": {"top": 4, "bottom": 3, "footwear": 3, "outerwear": 2, "bag": 1},
            "covered_elements": [
                {"id": "medieval-layering-vest", "label": "有结构感的叠穿马甲", "matching_item_ids": ["vest"]},
                {"id": "medieval-grounded-boots", "label": "皮革感靴履", "matching_item_ids": ["boots"]},
            ],
            "gaps": [
                {
                    "id": "medieval-long-silhouette",
                    "label": "长线条裙装",
                    "priority": "high",
                    "suggestion": "补充一件长裙或长连衣裙。",
                }
            ],
            "gap_count": 1,
            "redundancies": [],
            "summary": "要形成中世纪灵感轮廓，当前最明确的缺口是长线条裙装。",
            "evidence": [{"source_id": "style-medieval-inspired"}],
            "limitations": [],
        },
        "used_item_ids": ["vest", "boots"],
        "evidence_source_ids": ["style-medieval-inspired"],
    }
    workflow, llm = _workflow(database_path, agent2)
    payload = workflow.execute(
        TaskExecutionInput(user_id="u", request="要搭配中世纪风格的话，我的衣柜还缺什么？")
    )

    assert payload["task_type"] == "wardrobe_gap"
    assert payload["result"]["analysis_mode"] == "targeted"
    assert payload["result"]["gaps"][0]["label"] == "长线条裙装"
    with database_session(database_path) as connection:
        stored = get_task_run(connection, user_id="u", run_id=payload["run_id"])
    assert stored is not None
    assert stored["status"] == "completed"
    assert "missing_elements 才是缺口的唯一事实范围" in llm.calls[2]["system"]
    _assert_three_agent_execution(payload, llm)


def test_gap_draft_must_match_agent1_missing_elements(db_dsn: str) -> None:
    database_path = _seed_database(db_dsn)
    invalid_agent2 = {
        "task_type": "wardrobe_gap",
        "status": "completed",
        "summary": "当前衣橱没有缺口",
        "result": {
            "status": "completed",
            "analysis_mode": "targeted",
            "target": {"style": "Medieval-Inspired 中世纪灵感风格"},
            "wardrobe_item_count": 13,
            "slot_counts": {
                "top": 4,
                "bottom": 3,
                "footwear": 3,
                "outerwear": 2,
                "bag": 1,
            },
            "covered_elements": [
                {"id": "medieval-layering-vest", "matching_item_ids": ["vest"]},
                {"id": "medieval-grounded-boots", "matching_item_ids": ["boots"]},
                {"id": "medieval-textured-accessory", "matching_item_ids": ["bag"]},
            ],
            "gaps": [],
            "gap_count": 0,
            "redundancies": [],
            "summary": "当前衣橱没有缺口。",
            "evidence": [{"source_id": "style-medieval-inspired"}],
            "limitations": [],
        },
        "used_item_ids": ["vest", "boots", "bag"],
        "evidence_source_ids": ["style-medieval-inspired"],
    }
    # Agent 3 hard-validates before the LLM critic runs; its failure recomposes
    # Agent 2 once with repair feedback, and a second violation then propagates.
    llm = ScriptedExtensionLlm(
        [
            intent_response("分析中世纪风格衣橱缺口"),
            invalid_agent2,
            invalid_agent2,
        ]
    )
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=KNOWLEDGE_ROOT,
        llm_client=llm,
    )

    with pytest.raises(ValueError, match="missing_elements 不一致"):
        workflow.execute(
            TaskExecutionInput(
                user_id="u",
                request="要搭配中世纪风格，我的衣柜还缺什么？",
            )
        )

    # agent1 + initial agent2 + one repair recompose; the re-critic hard
    # validation fails before any further LLM call.
    assert len(llm.calls) == 3


def test_extension_without_llm_fails_and_is_persisted(db_dsn: str) -> None:
    database_path = _seed_database(db_dsn)
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=KNOWLEDGE_ROOT,
        llm_client=None,
    )

    with pytest.raises(LlmUnavailable, match="Agent 1 requires"):
        workflow.execute(TaskExecutionInput(user_id="u", request="黑色马甲怎么搭？"))

    with database_session(database_path) as connection:
        stored = connection.execute(
            "SELECT status, error_message FROM task_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert stored["status"] == "failed"
    assert "Agent 1 requires" in stored["error_message"]


def test_needs_clarification_is_persisted_as_a_terminal_status(db_dsn: str) -> None:
    database_path = _seed_database(db_dsn)
    agent2 = {
        "task_type": "item_advice",
        "status": "needs_clarification",
        "summary": "需要确认具体单品",
        "result": {
            "status": "needs_clarification",
            "knowledge_type": "item",
            "title": "请补充单品信息",
            "summary": "当前描述无法解析出明确单品。",
            "anchor_item": None,
            "anchor_source": "unresolved",
            "compatible_items_by_slot": {},
            "wardrobe_matches": [],
            "wardrobe_matches_by_slot": {},
            "sample_outfits": [],
            "evidence": [],
            "clarification_question": "请补充单品的品类和颜色。",
            "limitations": ["缺少明确单品"],
        },
        "used_item_ids": [],
        "evidence_source_ids": [],
    }
    workflow, llm = _workflow(database_path, agent2)

    payload = workflow.execute(
        TaskExecutionInput(
            user_id="u",
            request="这个怎么搭？",
            requested_task_type=TaskType.ITEM_ADVICE,
        )
    )

    assert payload["status"] == "needs_clarification"
    with database_session(database_path) as connection:
        stored = get_task_run(connection, user_id="u", run_id=payload["run_id"])
    assert stored is not None
    assert stored["status"] == "needs_clarification"
    _assert_three_agent_execution(payload, llm)


def test_agent2_repairs_incomplete_item_advice_before_critic(db_dsn: str) -> None:
    database_path = _seed_database(db_dsn)
    invalid_draft = {
        "task_type": "item_advice",
        "status": "needs_clarification",
        "summary": "缺少场合信息",
        "result": {
            "status": "needs_clarification",
            "knowledge_type": "item",
            "title": "黑色马甲搭配",
            "summary": "请补充场合。",
            "anchor_item": {"item_id": "candidate-preview", "name": "black vest"},
            "anchor_source": "candidate",
            "compatible_items_by_slot": {
                "bottom": [{"item_id": "trousers", "name": "Black tailored trousers"}]
            },
            "wardrobe_matches": [],
            "wardrobe_matches_by_slot": {},
            "sample_outfits": [],
            "evidence": [{"source_id": "item-vest"}],
            "clarification_question": "请补充穿着场合。",
            "limitations": [],
        },
        "used_item_ids": ["trousers"],
        "evidence_source_ids": ["item-vest"],
    }
    llm = ScriptedExtensionLlm(
        [
            intent_response("黑色马甲通用搭配"),
            invalid_draft,
            _item_advice_draft(),
            approved_review(),
        ]
    )
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=KNOWLEDGE_ROOT,
        llm_client=llm,
    )

    payload = workflow.execute(TaskExecutionInput(user_id="u", request="黑色马甲怎么搭？"))

    assert payload["status"] == "completed"
    assert payload["result"]["sample_outfits"]
    assert payload["llm_call_count"] == 4
    assert payload["diagnostics"]["agent2"]["call_count"] == 2
    assert "needs_clarification is forbidden" in llm.calls[2]["user"]


def test_agent3_rejection_recomposes_once_and_reviews_again(db_dsn: str) -> None:
    database_path = _seed_database(db_dsn)
    first_draft = _item_advice_draft(summary="给出一套基础组合")
    revised_draft = _item_advice_draft(summary="补充锚点、分层逻辑和完整通用搭配")
    llm = ScriptedExtensionLlm(
        [
            intent_response("黑色马甲通用搭配"),
            first_draft,
            {
                "approved": False,
                "grounded": True,
                "summary": "说明不够具体",
                "issues": ["需要解释马甲、内搭、下装和鞋履之间的组合逻辑"],
            },
            revised_draft,
            approved_review("重做方案已完整回答怎么搭"),
        ]
    )
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=KNOWLEDGE_ROOT,
        llm_client=llm,
    )

    payload = workflow.execute(TaskExecutionInput(user_id="u", request="黑色马甲怎么搭？"))

    assert payload["status"] == "completed"
    assert payload["result"]["summary"] == "补充锚点、分层逻辑和完整通用搭配"
    assert payload["llm_call_count"] == 5
    assert [item["node"] for item in payload["trace"]][-3:] == [
        "critic_agent",
        "composer_agent",
        "critic_agent",
    ]
    assert payload["trace"][-3]["status"] == "rejected"
    assert payload["trace"][-1]["status"] == "completed"


def test_item_advice_prunes_out_of_scope_llm_references(db_dsn: str) -> None:
    """LLM hallucinated same-series variant IDs are pruned to the candidate scope
    instead of hard-failing the extension validator (regression for tank-top 422)."""
    database_path = _seed_database(db_dsn)
    dirty_draft = _item_advice_draft(summary="越界联想同系列背心变体")
    dirty_draft["result"]["compatible_items_by_slot"]["top"].append(
        {"item_id": "P00893231", "name": "同系列背心变体"}
    )
    dirty_draft["result"]["sample_outfits"][0]["item_ids"] = [
        "candidate-preview",
        "shirt",
        "trousers",
        "loafers",
        "P00893231",
    ]
    dirty_draft["used_item_ids"] = ["shirt", "trousers", "loafers", "P00893231"]
    workflow, llm = _workflow(database_path, dirty_draft)
    payload = workflow.execute(
        TaskExecutionInput(
            user_id="u",
            request="以黑色马甲为锚点，搭配一整套",
            requested_task_type=TaskType.ITEM_ADVICE,
        )
    )

    assert payload["status"] == "completed"
    referenced = []
    referenced += payload["result"]["sample_outfits"][0]["item_ids"]
    for items in payload["result"]["compatible_items_by_slot"].values():
        referenced += [item["item_id"] for item in items]
    assert "P00893231" not in referenced
    assert "candidate-preview" in payload["result"]["sample_outfits"][0]["item_ids"]
    assert "shirt" in payload["result"]["sample_outfits"][0]["item_ids"]
    assert payload["diagnostics"]["agent2"]["call_count"] == 1  # no repair needed
    _assert_three_agent_execution(payload, llm)


def test_item_advice_fully_out_of_scope_triggers_repair(db_dsn: str) -> None:
    """When pruning empties every slot and outfit, Agent 2 retries once with a
    repair hint instead of returning a broken draft or hard-failing."""
    database_path = _seed_database(db_dsn)
    broken_draft = _item_advice_draft(summary="全部引用越界变体")
    broken_draft["result"]["compatible_items_by_slot"] = {
        "top": [{"item_id": "P00893231", "name": "越界背心"}]
    }
    broken_draft["result"]["sample_outfits"] = [
        {
            "outfit_id": "broken-1",
            "item_ids": ["candidate-preview", "P00893231"],
            "reasoning": "补全同系列背心",
        }
    ]
    broken_draft["used_item_ids"] = ["P00893231"]
    llm = ScriptedExtensionLlm(
        [
            intent_response("黑色马甲通用搭配"),
            broken_draft,
            _item_advice_draft(),
            approved_review(),
        ]
    )
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=KNOWLEDGE_ROOT,
        llm_client=llm,
    )

    payload = workflow.execute(
        TaskExecutionInput(
            user_id="u",
            request="黑色马甲怎么搭？",
            requested_task_type=TaskType.ITEM_ADVICE,
        )
    )

    assert payload["status"] == "completed"
    assert payload["result"]["sample_outfits"][0]["item_ids"] == [
        "candidate-preview",
        "shirt",
        "trousers",
        "loafers",
    ]
    assert payload["diagnostics"]["agent2"]["call_count"] == 2
    assert "候选范围外" in llm.calls[2]["user"]


def test_flexible_hard_validation_failure_triggers_repair_recompose(
    db_dsn: str,
) -> None:
    """A hard-validation failure in Agent 3 (too few alternatives) must recompose
    Agent 2 once with the validator message as ``repair_feedback`` instead of
    failing the whole task run, then re-critic to completion."""
    database_path = _seed_database(db_dsn)
    two_alt_draft = {
        "task_type": "outfit_modify",
        "status": "completed",
        "summary": "年轻一点的调整",
        "result": {
            "status": "completed",
            "current_outfit_id": "outfit-1",
            "target_slot": "",
            "replaced_item_ids": [],
            "locked_item_ids": [],
            "alternatives": [
                {
                    "outfit_id": "alt-1",
                    "item_ids": ["tee", "jeans", "sneakers"],
                    "reasoning": "换成复古 T 恤与牛仔裤",
                },
                {
                    "outfit_id": "alt-2",
                    "item_ids": ["shirt", "jeans", "sneakers"],
                    "reasoning": "白衬衫配牛仔裤",
                },
            ],
            "message": "",
        },
        "used_item_ids": ["tee", "jeans", "sneakers", "shirt"],
        "evidence_source_ids": [],
    }
    three_alt_draft = {
        "task_type": "outfit_modify",
        "status": "completed",
        "summary": "年轻一点的调整（重做）",
        "result": {
            "status": "completed",
            "current_outfit_id": "outfit-1",
            "target_slot": "",
            "replaced_item_ids": [],
            "locked_item_ids": [],
            "alternatives": [
                {
                    "outfit_id": "alt-1",
                    "item_ids": ["tee", "jeans", "sneakers"],
                    "reasoning": "换成复古 T 恤与牛仔裤",
                },
                {
                    "outfit_id": "alt-2",
                    "item_ids": ["knit", "skirt", "boots"],
                    "reasoning": "针织衫配百褶裙和靴子",
                },
                {
                    "outfit_id": "alt-3",
                    "item_ids": ["shirt", "trousers", "sneakers"],
                    "reasoning": "白衬衫配直筒西裤，保留正式感",
                },
            ],
            "message": "",
        },
        "used_item_ids": [
            "tee", "jeans", "sneakers", "knit", "skirt", "boots", "shirt",
            "trousers",
        ],
        "evidence_source_ids": [],
    }
    llm = ScriptedExtensionLlm(
        [
            intent_response("年轻一点的整体调整"),
            two_alt_draft,
            three_alt_draft,
            approved_review(),
            {"evidence": []},  # memory extraction
        ]
    )
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=KNOWLEDGE_ROOT,
        llm_client=llm,
    )
    session_context = {
        "current_outfit_id": "outfit-1",
        "current_item_ids": ["shirt", "trousers", "loafers", "trench"],
    }

    payload = workflow.execute(
        TaskExecutionInput(user_id="u", request="年轻一点"),
        session_context=session_context,
    )

    assert payload["status"] == "completed"
    # The repaired 3-alternative draft is what survives.
    assert len(payload["result"]["alternatives"]) == 3
    # composer (repair) + critic (re-run) = 2 extra calls over the happy path.
    assert payload["llm_call_count"] == 4
    assert payload["diagnostics"]["agent3_initial"]["agent2_repair"]["call_count"] == 1
    assert "硬校验" in llm.calls[2]["user"]
