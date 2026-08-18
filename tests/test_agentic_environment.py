"""Deterministic Environment: facts freezing, draft mutation, legality gate.

The Environment supplies facts and executes tools; it never decides what the
user wants. This test pins: build_facts grounding (active outfit / selected
item / weather / memory), the fallback to stored candidate outfits, search
top-K capping, modify_outfit full-state validation (FAIL leaves the draft
untouched), and check_environment surfacing UNKNOWN structures.

The environment holds a live connection, so the tests use a fixture that keeps
``database_session`` open for the whole test rather than closing it early.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterator

import pytest

from styleforge.agentic.environment import (
    Draft,
    Environment,
    build_facts,
    resolve_active_outfit,
)
from styleforge.models.agentic_contract import (
    BodyRegion,
    GarmentLayer,
    ItemSnapshot,
    ModifyOp,
    ModifyPlan,
    Placement,
)
from styleforge.models.context import (
    ContextPack,
    EnvironmentContext,
    RequestContext,
    UserContext,
)
from styleforge.models.task import TaskExecutionInput
from styleforge.repositories.catalog_repository import upsert_items
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.wardrobe_repository import add_items, list_items

from tests.helpers import make_item


def _seed(database_path: str) -> None:
    items = [
        make_item("shirt_a", "top", "White shirt", "white"),
        make_item("pants_b", "pants", "Black trousers", "black"),
        make_item("boots_c", "shoes", "Brown boots", "brown"),
        make_item("sneakers_d", "shoes", "White sneakers", "white"),
        make_item("coat_e", "outwear", "Gray coat", "gray"),
        make_item("bag_f", "bag", "Black bag", "black"),
        make_item("cape_x", "cape", "Long cape", "navy"),  # UNKNOWN type
    ]
    with database_session(database_path) as connection:
        upsert_items(connection, items, "test")
        add_items(connection, "u", [item.item_id for item in items])


@pytest.fixture()
def seeded_conn(db_dsn: str) -> Iterator[Any]:
    """An initialized schema with a live connection kept open for the test."""
    initialize_database(db_dsn)
    _seed(db_dsn)
    with database_session(db_dsn) as connection:
        yield connection


def _context_pack() -> ContextPack:
    return ContextPack(
        request_context=RequestContext(
            original_request="换双鞋",
            task_type="OUTFIT_MODIFY",
            route_reason="test",
            route_confidence=1.0,
        ),
        user_context=UserContext(
            user_id="u",
            preferences={"memory_profile": {"style": "极简"}},
        ),
        environment_context=EnvironmentContext(weather={"summary": "sunny", "temp": 20}),
    )


def _input(**overrides) -> TaskExecutionInput:
    base = dict(
        user_id="u",
        request="换双鞋",
        current_outfit_id="o1",
        current_item_ids=["shirt_a", "pants_b", "boots_c"],
    )
    base.update(overrides)
    return TaskExecutionInput(**base)


def _env(connection: Any, task_input: TaskExecutionInput) -> Environment:
    wardrobe_items = list_items(connection, task_input.user_id)
    facts = build_facts(connection, task_input, _context_pack(), wardrobe_items)
    return Environment(connection, wardrobe_items, facts)


# ── build_facts ──────────────────────────────────────────────────────


def test_build_facts_grounds_active_outfit_and_selected_item(seeded_conn: Any) -> None:
    env = _env(seeded_conn, _input(selected_item_id="shirt_a"))
    facts = env.facts

    assert facts.interaction.active_outfit_id == "o1"
    assert facts.interaction.selected_item_id == "shirt_a"
    assert facts.active_outfit is not None
    assert facts.active_outfit.outfit_id == "o1"
    assert facts.active_outfit.item_ids == ["shirt_a", "pants_b", "boots_c"]
    assert len(facts.active_outfit.items) == 3
    assert facts.selected_item is not None
    assert facts.selected_item.item_id == "shirt_a"
    assert facts.selected_item.item_type == "top"
    assert facts.weather == {"summary": "sunny", "temp": 20}
    assert facts.memory_profile == {"style": "极简"}


def test_build_facts_normalises_list_memory_profile(seeded_conn: Any) -> None:
    """Real users carry preference evidence as a *list* (list_preferences);
    the program boundary must snapshot it into the dict the contract expects
    instead of tripping EnvironmentFacts validation."""
    evidence = [
        {"preference_id": 97, "preference": "不喜欢运动鞋", "created_at": "2026-08-17T00:00:00+00:00"},
        {"preference_id": 98, "preference": "偏好深色系", "created_at": "2026-08-17T00:00:00+00:00"},
    ]
    pack = ContextPack(
        request_context=RequestContext(
            original_request="换双鞋",
            task_type="OUTFIT_MODIFY",
            route_reason="test",
            route_confidence=1.0,
        ),
        user_context=UserContext(
            user_id="u",
            preferences={"memory_profile": evidence},
        ),
        environment_context=EnvironmentContext(weather={"summary": "sunny", "temp": 20}),
    )
    wardrobe_items = list_items(seeded_conn, "u")
    facts = build_facts(seeded_conn, _input(), pack, wardrobe_items)

    assert facts.memory_profile == {"preferences": evidence}


def test_build_facts_empty_memory_profile_stays_dict(seeded_conn: Any) -> None:
    """An empty list (no memories yet) must not produce a broken dict."""
    pack = ContextPack(
        request_context=RequestContext(
            original_request="换双鞋",
            task_type="OUTFIT_MODIFY",
            route_reason="test",
            route_confidence=1.0,
        ),
        user_context=UserContext(user_id="u", preferences={"memory_profile": []}),
        environment_context=EnvironmentContext(weather={"summary": "sunny", "temp": 20}),
    )
    wardrobe_items = list_items(seeded_conn, "u")
    facts = build_facts(seeded_conn, _input(), pack, wardrobe_items)

    assert facts.memory_profile == {}


def test_build_facts_wardrobe_summary_counts_by_type(seeded_conn: Any) -> None:
    facts = _env(seeded_conn, _input()).facts
    assert facts.wardrobe_summary["top"]["count"] == 1
    assert facts.wardrobe_summary["shoes"]["count"] == 2
    assert set(facts.wardrobe_summary["shoes"]["sample_colors"]) == {"brown", "white"}


def test_resolve_active_outfit_falls_back_to_stored_candidate(seeded_conn: Any) -> None:
    now = datetime.now(timezone.utc).isoformat()
    seeded_conn.execute(
        """
        INSERT INTO styling_runs (run_id, user_id, status, task_spec_json, created_at)
        VALUES (%s, %s, %s, %s, %s)
        """,
        ("r_hist", "u", "completed", "{}", now),
    )
    seeded_conn.execute(
        """
        INSERT INTO candidate_outfits
        (run_id, outfit_id, rank, score, hard_valid, item_ids_json, score_details_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            "r_hist",
            "o_hist",
            1,
            0.8,
            1,
            json.dumps(["shirt_a", "pants_b", "boots_c"]),
            "{}",
        ),
    )
    # No item_ids travel with the request — only an outfit_id.
    task_input = _input(current_item_ids=[], current_outfit_id="o_hist")
    active = resolve_active_outfit(seeded_conn, task_input, _context_pack())

    assert active is not None
    assert active.outfit_id == "o_hist"
    assert active.item_ids == ["shirt_a", "pants_b", "boots_c"]
    assert len(active.items) == 3


def test_visible_outfits_are_recent_completed_candidates(seeded_conn: Any) -> None:
    now = datetime.now(timezone.utc).isoformat()
    seeded_conn.execute(
        """
        INSERT INTO styling_runs (run_id, user_id, status, task_spec_json, created_at)
        VALUES (%s, %s, %s, %s, %s)
        """,
        ("r_recent", "u", "completed", "{}", now),
    )
    seeded_conn.execute(
        """
        INSERT INTO candidate_outfits
        (run_id, outfit_id, rank, score, hard_valid, item_ids_json, score_details_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            "r_recent",
            "o_recent",
            1,
            0.9,
            1,
            json.dumps(["shirt_a", "pants_b", "boots_c"]),
            "{}",
        ),
    )
    facts = _env(seeded_conn, _input()).facts
    assert any(o.outfit_id == "o_recent" for o in facts.visible_outfits)


# ── tools ────────────────────────────────────────────────────────────


def _draft_from_active(seeded_conn: Any) -> tuple[Environment, Draft]:
    env = _env(seeded_conn, _input())
    draft = Draft(outfit=env.facts.active_outfit, layers={})
    return env, draft


def test_search_wardrobe_matches_and_caps_at_top_k(seeded_conn: Any) -> None:
    env, _ = _draft_from_active(seeded_conn)
    env.search_limit = 1  # hard cap

    result = env.search_wardrobe("shoes")
    assert result.matched == 2  # boots_c + sneakers_d
    assert len(result.results) == 1  # capped at top-K
    assert result.results[0].item_type == "shoes"


def test_search_wardrobe_empty_is_a_fact(seeded_conn: Any) -> None:
    env, _ = _draft_from_active(seeded_conn)
    result = env.search_wardrobe("sombrero")
    assert result.matched == 0
    assert result.results == []


def test_modify_outfit_add_conflict_is_rejected_and_draft_untouched(seeded_conn: Any) -> None:
    env, draft = _draft_from_active(seeded_conn)
    before_ids = list(draft.outfit.item_ids)
    plan = ModifyPlan(
        ops=[
            ModifyOp(
                action="add",
                item_id="sneakers_d",
                placement=Placement(region=BodyRegion.feet, layer=GarmentLayer.base),
            )
        ]
    )
    next_draft, issues = env.modify_outfit(draft, plan)
    assert next_draft is None
    assert issues  # two pairs of shoes conflict
    assert draft.outfit.item_ids == before_ids  # untouched


def test_modify_outfit_add_without_placement_is_filled_from_structure(seeded_conn: Any) -> None:
    # A real model may omit placement entirely. The environment derives region
    # from the garment type and layer from its effective (lowest) layer.
    env, draft = _draft_from_active(seeded_conn)
    plan = ModifyPlan(
        ops=[
            ModifyOp(action="add", item_id="sneakers_d"),
            ModifyOp(action="remove", item_id="boots_c"),
        ]
    )
    next_draft, issues = env.modify_outfit(draft, plan)
    assert issues == []
    assert next_draft is not None
    assert "sneakers_d" in next_draft.outfit.item_ids
    assert next_draft.layers["sneakers_d"] is GarmentLayer.base  # feet garment


def test_modify_outfit_add_accessory_is_legal(seeded_conn: Any) -> None:
    env, draft = _draft_from_active(seeded_conn)
    plan = ModifyPlan(
        ops=[
            ModifyOp(
                action="add",
                item_id="bag_f",
                placement=Placement(region=BodyRegion.accessory, layer=GarmentLayer.base),
            )
        ]
    )
    next_draft, issues = env.modify_outfit(draft, plan)
    assert issues == []
    assert next_draft is not None
    assert "bag_f" in next_draft.outfit.item_ids
    assert len(next_draft.outfit.items) == 4


def test_modify_outfit_replace_is_legal(seeded_conn: Any) -> None:
    env, draft = _draft_from_active(seeded_conn)
    plan = ModifyPlan(
        ops=[
            ModifyOp(
                action="replace",
                item_id="boots_c",
                replacement_item_id="sneakers_d",
                placement=Placement(region=BodyRegion.feet, layer=GarmentLayer.base),
            )
        ]
    )
    next_draft, issues = env.modify_outfit(draft, plan)
    assert issues == []
    assert next_draft is not None
    assert "boots_c" not in next_draft.outfit.item_ids
    assert "sneakers_d" in next_draft.outfit.item_ids


def test_modify_outfit_remove_missing_is_rejected(seeded_conn: Any) -> None:
    env, draft = _draft_from_active(seeded_conn)
    plan = ModifyPlan(ops=[ModifyOp(action="remove", item_id="ghost_item")])
    next_draft, issues = env.modify_outfit(draft, plan)
    assert next_draft is None
    assert "ghost_item" in issues[0]


def test_modify_outfit_illegal_placement_is_rejected(seeded_conn: Any) -> None:
    env, draft = _draft_from_active(seeded_conn)
    plan = ModifyPlan(
        ops=[
            ModifyOp(
                action="add",
                item_id="coat_e",
                placement=Placement(region=BodyRegion.lower_body, layer=GarmentLayer.base),
            )
        ]
    )
    next_draft, issues = env.modify_outfit(draft, plan)
    assert next_draft is None
    assert issues


def test_modify_outfit_add_from_outside_wardrobe_is_rejected(seeded_conn: Any) -> None:
    env, draft = _draft_from_active(seeded_conn)
    plan = ModifyPlan(
        ops=[
            ModifyOp(
                action="add",
                item_id="not-in-wardrobe",
                placement=Placement(region=BodyRegion.feet, layer=GarmentLayer.base),
            )
        ]
    )
    next_draft, issues = env.modify_outfit(draft, plan)
    assert next_draft is None
    assert "not-in-wardrobe" in issues[0]


def test_check_environment_surfaces_unknown_structures(seeded_conn: Any) -> None:
    env, draft = _draft_from_active(seeded_conn)
    # A candidate containing an UNKNOWN-type item: structure is unknown, not a
    # (region,layer) conflict — but it IS surfaced to the Agent as an issue.
    unknown = ItemSnapshot(item_id="cape_x", item_type="cape", name="Long cape")
    draft = Draft(
        outfit=draft.outfit.model_copy(
            update={
                "item_ids": draft.outfit.item_ids + ["cape_x"],
                "items": list(draft.outfit.items) + [unknown],
            }
        ),
        layers={},
    )
    result = env.check_environment(draft)
    assert result.valid is False
    assert any("cape_x" in issue for issue in result.issues)


# ── search_web (web search beyond the wardrobe) ──────────────────────


def test_search_web_without_provider_degrades(seeded_conn: Any) -> None:
    # No provider is the default: the tool must not crash, it returns an
    # "unconfigured" fact the Agent works around.
    env, _ = _draft_from_active(seeded_conn)
    result = env.search_web("海边度假穿什么")

    assert result.available is False
    assert "未配置" in result.error
    assert result.results == []


def test_search_web_delegates_to_provider(seeded_conn: Any) -> None:
    from styleforge.models.agentic_contract import WebSearchHit, WebSearchResult

    class StubProvider:
        def __init__(self) -> None:
            self.queries: list[str] = []

        def search(self, query: str) -> WebSearchResult:
            self.queries.append(query)
            return WebSearchResult(
                query=query,
                results=[WebSearchHit(title="快干材质更适合海边", content="建议速干短裤", url="u")],
            )

    stub = StubProvider()
    env, _ = _draft_from_active(seeded_conn)
    env.web_search_provider = stub
    result = env.search_web(" 海边度假穿什么 ")

    assert stub.queries == ["海边度假穿什么"]  # trimmed at the boundary
    assert result.available is True
    assert result.results[0].title == "快干材质更适合海边"
