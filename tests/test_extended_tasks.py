"""Pure-function tests for the extension analysis / validation helpers.

The four extension task types (STYLE_ADVICE / ITEM_ADVICE /
WARDROBE_COMPATIBILITY / WARDROBE_GAP) now run through the Multi-Agent Harness;
the end-to-end chain tests live in ``test_agentic_extension_primary.py``. What
remains here are the deterministic fact-layer helpers the Extension subgraph
builds on — ``analyze_extension_task`` facts, the flexible-rebuild validator
and sanitizer, the Agent 2 system prompt, and the bounded candidate pool.
"""

from __future__ import annotations

from pathlib import Path

from styleforge.models.agent_tasks import Agent1TaskOutput
from styleforge.models.task import TaskExecutionInput
from styleforge.orchestration.task_router import TaskRouter, TaskType
from styleforge.repositories.catalog_repository import upsert_items
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.wardrobe_repository import add_items
from styleforge.tools.extension_analysis import analyze_extension_task
from styleforge.tools.extension_validation import _validate_flexible

from tests.helpers import make_item


KNOWLEDGE_ROOT = Path("knowledge")


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
    # silently substituting any outerwear.  Relaxation/asking is the Agent's
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
    pool must surface that type's items by name so the Agent can pick a hat
    rather than any accessory (regression for "加帽子却给戒指")."""
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
    blows the Agent prompt context) and keep every core slot represented so the
    composer can rebuild a complete outfit (regression for the two-shoes /
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
