"""Agent 1 wiring tests: CandidatePool -> structured Agent1TaskOutput facts.

These live apart from ``test_candidate_service`` because they pin the contract
of the *tool* (``_structured_modify`` in tools/extension_analysis.py), not the
pure fact layer: feasibility report, relaxation plan, the effective request_spec
(with a resolved anaphor no longer flagged unresolved), and the bounded
candidate scope the hard validator enforces.
"""

from __future__ import annotations

from styleforge.core.candidate_service import FeasibilityState, build_candidate_pool
from styleforge.core.request_spec import (
    Change,
    ChangeAction,
    ConstraintStrength,
    EntityRef,
    Operation,
    RequestSpec,
    interpret_request,
)
from styleforge.core.schemas import CatalogItem, ImageStatus


def _item(
    item_id: str,
    item_type: str,
    *,
    name: str = "",
    color: str = "",
    description: str = "",
    features: tuple[str, ...] = (),
) -> CatalogItem:
    return CatalogItem(
        item_id=item_id,
        source="test",
        gender="",
        item_type=item_type,
        main_category=item_type,
        name=name,
        color=color,
        description=description,
        features=features,
        image_filename="",
        relative_image_path="",
        image_status=ImageStatus.UNBOUND,
    )


def _modify_spec(
    changes: list[Change],
    *,
    locks: list | None = None,
    excluded_colors: list[str] | None = None,
    unresolved: list[str] | None = None,
) -> RequestSpec:
    return RequestSpec(
        operation=Operation.MODIFY,
        changes=changes,
        locks=locks or [],
        excluded_colors=excluded_colors or [],
        unresolved_fields=unresolved or [],
    )


def _change(
    action: ChangeAction,
    *,
    item_type: str | None = None,
    subtype: str | None = None,
    slot: str | None = None,
    source_text: str = "",
    strength: ConstraintStrength = ConstraintStrength.MUST,
) -> Change:
    return Change(
        action=action,
        target=EntityRef(item_type=item_type, subtype=subtype, slot=slot),
        strength=strength,
        source_text=source_text,
    )


def _make_original_wardrobe() -> list[CatalogItem]:
    return [
        _item("dress", "dress", name="Little black dress", color="black"),
        _item("shoes", "shoes", name="Black heels", color="black"),
        _item("earrings", "earrings", name="Stud earrings", color="gold"),
        _item("rings", "rings", name="Statement ring", color="silver"),
        _item("bag", "bag", name="Leather handbag", color="brown"),
        # The failing item: a hair clip typed as "other" (loose catalog data).
        _item("purple_clip", "other", name="Purple hair clip", color="purple",
              description="", features=("hair clip",)),
        # Hats: must never enter the pool after "不要帽子".
        _item("hat1", "hats", name="Fedora", color="brown"),
        _item("hat2", "hats", name="Beanie", color="black"),
        # Hairwear candidates.
        _item("pink_clip", "hairwear", name="Pink barrette", color="pink",
              description="", features=("hair clip",)),
        _item("gold_clip", "hairwear", name="Gold barrette", color="gold",
              description="", features=("hair clip",)),
    ]


CURRENT_IDS = ["dress", "shoes", "earrings", "rings", "bag", "purple_clip"]


def _route():
    from styleforge.orchestration.task_router import (
        TASK_CAPABILITIES,
        TASK_SUBGRAPHS,
        TaskRoute,
        TaskType,
    )

    return TaskRoute(
        task_type=TaskType.OUTFIT_MODIFY,
        subgraph=TASK_SUBGRAPHS[TaskType.OUTFIT_MODIFY],
        confidence=1.0,
        reason="test",
        required_capabilities=TASK_CAPABILITIES[TaskType.OUTFIT_MODIFY],
    )


def test_structured_modify_wires_candidate_pool_into_agent1_contract() -> None:
    from styleforge.tools.extension_analysis import _structured_modify

    spec = interpret_request("不要帽子，要粉色系发夹")
    pool = build_candidate_pool(spec, _make_original_wardrobe(), CURRENT_IDS)
    output = _structured_modify("outfit-1", CURRENT_IDS, _make_original_wardrobe(), pool, spec, _route())

    assert output.task_type.value == "outfit_modify"
    # intent summary derived from the change set, not a target_slot.
    assert "帽子" in output.intent_summary
    assert "发夹" in output.intent_summary
    # Everything not mentioned is locked+kept; nothing is replaced (no hat in
    # the current outfit).  This is exactly the contract the hard validator
    # enforces: locked kept, replaced removed, replacements from the pool only.
    assert set(output.facts["locked_item_ids"]) == {
        "dress", "shoes", "earrings", "rings", "bag", "purple_clip"
    }
    assert output.facts["replaced_item_ids"] == []
    assert output.facts["adjustment_mode"] == "structured"
    assert output.facts["replacement_item_ids"] == pool.candidate_item_ids
    # Feasibility + parsed intent are surfaced as facts, never as a decision.
    report = output.facts["feasibility_report"]
    assert report["state"] == FeasibilityState.EXACT.value
    assert report["excluded_item_types"] == [{"item_type": "hats", "subtype": None}]
    # The generic relaxation policy rides along for Agent 2's decision.  The
    # pink-clip change is exact (level 0), drops only its PREFER colour when a
    # non-pink candidate is acceptable (level 1), and broadens to the accessory
    # slot for outfit-building options (level 3).  Pink exists in this wardrobe
    # (pink_clip), so no preference gap is reported.
    plan = report["relaxation_plan"]
    assert plan["feasibility"] == FeasibilityState.EXACT.value
    option = next(
        o for o in plan["options"] if o["change_id"] == "ADD_OR_REPLACE-0"
    )
    assert option["minimal_level"] == 0
    assert option["unmet_prefer_colors"] == []
    assert {level["level"] for level in option["chain"]} == {0, 1, 3}
    levels = {level["level"]: level for level in option["chain"]}
    assert levels[1]["kind"] == "color"
    assert "粉色" in levels[1]["dropped"]
    assert levels[1]["candidate_ids"] == ["gold_clip"]
    assert "request_spec" in output.facts
    # Candidate scope for the hard validator = the full bounded pool.
    assert output.candidate_item_ids == pool.candidate_item_ids
    # Named cards expose each candidate's type/name for Agent 2.
    texts = {card["item_id"]: card for card in output.facts["candidate_item_texts"]}
    assert texts["pink_clip"]["item_type"] == "hairwear"
    assert texts["pink_clip"]["name"] == "Pink barrette"


def test_structured_modify_reports_unsatisfiable_without_deciding() -> None:
    from styleforge.tools.extension_analysis import _structured_modify

    spec = _modify_spec(
        [_change(ChangeAction.ADD_OR_REPLACE, item_type="hairwear", source_text="发夹")]
    )
    pool = build_candidate_pool(
        spec,
        [_item("dress", "dress", name="Dress"), _item("hat1", "hats", name="Hat")],
        ["dress"],
    )
    output = _structured_modify("outfit-1", ["dress"], [], pool, spec, _route())

    assert output.facts["feasibility_report"]["state"] == FeasibilityState.UNSATISFIABLE.value
    assert output.facts["feasibility_report"]["unmet_constraints"] == ["发夹"]
    # Facts only: Agent 2 still gets a bounded pool and must decide what to do.
    assert isinstance(output.candidate_item_ids, list)


def test_resolved_anaphor_not_kept_as_effective_unresolved() -> None:
    """"把这件大衣换成西装" inside a session whose current outfit holds a coat:
    CandidateService anchors the 大衣 subject to the concrete coat (feasibility
    EXACT), so the request_spec fact Agent 2 receives must NOT still list
    anaphoric_reference in unresolved_fields -- that stale obligation would
    make Agent 2 bounce back for clarification the caller already resolved."""
    from styleforge.tools.extension_analysis import _structured_modify

    wardrobe = [
        _item("coat", "outwear", name="Beige coat", color="beige"),
        _item("suit", "suit", name="Navy suit", color="navy"),
        _item("shoes", "shoes", name="Black shoes", color="black"),
    ]
    spec = interpret_request("把这件大衣换成西装")
    assert "anaphoric_reference" in spec.unresolved_fields
    pool = build_candidate_pool(spec, wardrobe, ["coat", "shoes"])
    assert pool.feasibility is not FeasibilityState.NEEDS_CLARIFICATION
    output = _structured_modify("outfit-1", ["coat", "shoes"], wardrobe, pool, spec, _route())

    fact_spec = output.facts["request_spec"]
    assert "anaphoric_reference" not in fact_spec["unresolved_fields"]
    # The resolved subject still drives the replacement fact.
    assert output.facts["replaced_item_ids"] == ["coat"]
