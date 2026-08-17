"""PR4A decision tests: the five Agent 2 decisions, derived purely from facts.

The decision is a factual classification of ``feasibility + relaxation plan``
(decomposed into unsatisfied-hard / relaxed-must / unsatisfied-soft), never
left to the LLM:

  * unresolved anaphor                          -> ASK_USER
  * MUST with no candidate at any level         -> WARDROBE_GAP
  * MUST exact-unmet but MUST-level relaxable   -> RETRIEVE_MORE (colour *or*
    type/slot -- any MUST dimension needs confirmation, never auto-relax)
  * EXACT + unmet PREFER colour                 -> RELAX_PREFERENCE (drop only
    the preference)
  * EXACT + all preferences met                 -> EXACT_MATCH

The original acceptance case is locked here end-to-end: 不要帽子，要粉色系发夹
with no pink clip -> RELAX_PREFERENCE relaxing *only* pink while the hairwear
MUST stays exact and hats never re-enter.
"""

from __future__ import annotations

from styleforge.agents.composer import ComposerAgent
from styleforge.core.candidate_service import build_candidate_pool
from styleforge.core.decision import (
    Agent2DecisionFacts,
    DecisionType,
    derive_decision,
    derive_decision_from_facts,
)
from styleforge.core.relaxation import build_relaxation_plan
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
from styleforge.models.context import ContextPack, RequestContext, UserContext
from styleforge.orchestration.task_router import TaskType

from tests.extension_llm import ScriptedExtensionLlm


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


def _hairwear_change(
    *,
    color_value: str,
    color_strength: ConstraintStrength,
) -> Change:
    return Change(
        action=ChangeAction.ADD_OR_REPLACE,
        target=EntityRef(item_type="hairwear", subtype="hair_clip"),
        strength=ConstraintStrength.MUST,
        preferences=[
            {
                "attribute": "color_family",
                "value": color_value,
                "strength": color_strength,
            }
        ],
    )


def _spec(changes: list[Change]) -> RequestSpec:
    return RequestSpec(
        operation=Operation.MODIFY,
        changes=changes,
        locks=[],
        excluded_colors=[],
        unresolved_fields=[],
    )


def _pink_clip() -> CatalogItem:
    return _item("pink_clip", "hairwear", name="Pink barrette", color="pink",
                 description="", features=("hair clip",))


def _gold_clip() -> CatalogItem:
    return _item("gold_clip", "hairwear", name="Gold barrette", color="gold",
                 description="", features=("hair clip",))


def _hat() -> CatalogItem:
    return _item("hat1", "hats", name="Fedora", color="brown")


def _decision_for(
    spec: RequestSpec,
    wardrobe: list[CatalogItem],
    current: list[str],
) -> tuple[object, object]:
    pool = build_candidate_pool(spec, wardrobe, current)
    plan = build_relaxation_plan(pool, spec, wardrobe)
    return derive_decision(
        Agent2DecisionFacts(feasibility=pool.feasibility, relaxation_plan=plan)
    ), pool


# ---------------------------------------------------------------------------
# The five decisions.
# ---------------------------------------------------------------------------


def test_exact_match_when_preferences_are_met() -> None:
    # The pink clip exists, so exact hairwear AND the pink preference are both
    # satisfiable: nothing to relax.
    spec = interpret_request("不要帽子，要粉色系发夹")
    decision, _ = _decision_for(
        spec,
        [_pink_clip(), _gold_clip(), _hat(), _item("dress", "dress", name="Dress")],
        ["dress"],
    )
    assert decision.decision is DecisionType.EXACT_MATCH
    assert decision.relaxed_prefers == []


def test_relax_preference_drops_only_the_unmet_prefer_color() -> None:
    """Original acceptance case: no pink clip -> the hairwear MUST stays exact
    (gold clip), the MUST_NOT hat stays excluded, and the decision surrenders
    *only* the PREFER pink."""
    spec = interpret_request("不要帽子，要粉色系发夹")
    wardrobe = [
        _item("dress", "dress", name="Little black dress", color="black"),
        _item("shoes", "shoes", name="Black heels", color="black"),
        _item("purple_clip", "other", name="Purple hair clip", color="purple",
              description="", features=("hair clip",)),
        _hat(),
        _gold_clip(),
        _item("necklace", "necklace", name="Gold necklace", color="gold"),
    ]
    current = ["dress", "shoes", "purple_clip"]
    decision, pool = _decision_for(spec, wardrobe, current)

    assert decision.decision is DecisionType.RELAX_PREFERENCE
    assert decision.relaxed_prefers == ["pink"]
    assert "hat1" not in pool.candidate_item_ids
    assert "gold_clip" in pool.candidate_item_ids


def test_must_color_gap_requires_more_retrieval() -> None:
    # The pink colour is MUST here, not PREFER: exact is empty and gold re-enters
    # only by dropping the MUST colour. That MUST-level relaxation is never
    # automatic, so the decision is RETRIEVE_MORE (colour dimension).
    spec = _spec([_hairwear_change(color_value="pink", color_strength=ConstraintStrength.MUST)])
    decision, _ = _decision_for(spec, [_gold_clip(), _item("dress", "dress", name="Dress")], ["dress"])

    assert decision.decision is DecisionType.RETRIEVE_MORE
    assert decision.relaxed_must  # the MUST hairwear change needs confirmation
    assert decision.unmet_must == []


def test_must_type_broadened_to_slot_is_retrieve_more_not_gap() -> None:
    """要发夹 with no hairwear at all but an accessory-slot earring: the exact
    MUST type is unmet, yet a MUST-level type relaxation (broaden hairwear to the
    accessory slot) unlocks a candidate.  Not a wardrobe gap -- coverage exists,
    only exact does not -- so the MUST dimension needs confirmation, not a gap
    report.  This is why the decision reads the plan, not the coarse status
    (which would say UNSATISFIABLE)."""
    spec = interpret_request("要发夹")
    decision, _ = _decision_for(
        spec,
        [_item("earrings", "earrings", name="Stud earrings", color="gold"),
         _item("dress", "dress", name="Dress")],
        ["dress"],
    )

    assert decision.decision is DecisionType.RETRIEVE_MORE
    assert "发夹" in decision.relaxed_must
    assert decision.unmet_must == []


def test_anaphora_asks_user() -> None:
    spec = interpret_request("把那个换掉")
    decision, _ = _decision_for(
        spec,
        [_item("dress", "dress", name="Dress"), _item("shoes", "shoes", name="Shoes")],
        ["dress", "shoes"],
    )
    assert decision.decision is DecisionType.ASK_USER
    assert decision.clarification_reason


def test_unmet_must_is_wardrobe_gap() -> None:
    # No hairwear at all: even type-broadening finds nothing, so the MUST 发夹 is
    # a gap that must be reported, not silently replaced by another accessory.
    spec = interpret_request("不要帽子，要发夹")
    decision, _ = _decision_for(spec, [_hat(), _item("dress", "dress", name="Dress")], ["dress"])

    assert decision.decision is DecisionType.WARDROBE_GAP
    assert "发夹" in decision.unmet_must


# ---------------------------------------------------------------------------
# Facts reconstruction (what the composer runs).
# ---------------------------------------------------------------------------


def _route() -> object:
    from styleforge.orchestration.task_router import (
        TASK_CAPABILITIES,
        TASK_SUBGRAPHS,
        TaskRoute,
    )

    return TaskRoute(
        task_type=TaskType.OUTFIT_MODIFY,
        subgraph=TASK_SUBGRAPHS[TaskType.OUTFIT_MODIFY],
        confidence=1.0,
        reason="test",
        required_capabilities=TASK_CAPABILITIES[TaskType.OUTFIT_MODIFY],
    )


def _acceptance_agent1():
    from styleforge.tools.extension_analysis import _structured_modify

    wardrobe = [
        _item("dress", "dress", name="Little black dress", color="black"),
        _item("shoes", "shoes", name="Black heels", color="black"),
        _item("purple_clip", "other", name="Purple hair clip", color="purple",
              description="", features=("hair clip",)),
        _hat(),
        _gold_clip(),
        _item("necklace", "necklace", name="Gold necklace", color="gold"),
    ]
    current = ["dress", "shoes", "purple_clip"]
    spec = interpret_request("不要帽子，要粉色系发夹")
    pool = build_candidate_pool(spec, wardrobe, current)
    return _structured_modify("outfit-1", current, wardrobe, pool, spec, _route()), wardrobe


def test_derive_decision_from_structured_facts_acceptance_case() -> None:
    agent1, _ = _acceptance_agent1()
    decision = derive_decision_from_facts(agent1.facts)

    assert decision is not None
    assert decision.decision is DecisionType.RELAX_PREFERENCE
    assert decision.relaxed_prefers == ["pink"]


def test_derive_decision_from_facts_none_outside_structured_mode() -> None:
    assert derive_decision_from_facts({}) is None
    assert derive_decision_from_facts({"adjustment_mode": "flexible"}) is None
    # Structured facts without the pool dump degrade to no decision rather than
    # fabricating one from half the picture.
    assert (
        derive_decision_from_facts({"adjustment_mode": "structured"})
        is None
    )


def test_composer_attaches_decision_and_keeps_it_out_of_the_llm_schema() -> None:
    """End-to-end Agent 2: the LLM never sees or fills ``decision`` (schema +
    prompt), and the validated output carries the deterministic RELAX_PREFERENCE
    with only pink surrendered."""
    agent1, _ = _acceptance_agent1()
    draft = {
        "task_type": "outfit_modify",
        "status": "completed",
        "summary": "候选无粉色发夹，保留金色发夹",
        "result": {
            "status": "completed",
            "current_outfit_id": "outfit-1",
            "target_slot": "",
            "replaced_item_ids": [],
            "locked_item_ids": ["dress", "shoes", "purple_clip"],
            "alternatives": [
                {
                    "outfit_id": "mod-1",
                    "item_ids": ["dress", "shoes", "purple_clip", "gold_clip"],
                    "reasoning": "用金色发夹满足发饰，粉色偏好不可得。",
                },
                {
                    "outfit_id": "mod-2",
                    "item_ids": ["dress", "shoes", "purple_clip", "necklace"],
                    "reasoning": "配金色项链完成配饰层次。",
                },
            ],
            "message": "粉色偏好暂时无法满足，已用金色替代。",
        },
        "used_item_ids": ["dress", "shoes", "purple_clip", "gold_clip"],
        "evidence_source_ids": [],
    }
    context = ContextPack(
        request_context=RequestContext(
            original_request="不要帽子，要粉色系发夹",
            task_type="outfit_modify",
            route_reason="test",
            route_confidence=1.0,
        ),
        user_context=UserContext(user_id="u"),
    )
    llm = ScriptedExtensionLlm([draft])
    output, _info, _diag = ComposerAgent().run_extension(
        user_query="不要帽子，要粉色系发夹",
        context_pack=context,
        agent1_output=agent1,
        llm=llm,
    )

    # Decision field is owned by the fact-derived classification.
    assert output.decision is not None
    assert output.decision.decision is DecisionType.RELAX_PREFERENCE
    assert output.decision.relaxed_prefers == ["pink"]

    # The LLM never had the decision in its schema, nor was it asked to fill it.
    call = llm.calls[0]
    assert "decision" not in call["json_schema"].get("properties", {})
    assert "当前决策" in call["user"]
    assert "RELAX_PREFERENCE" in call["user"]
    assert "允许放弃的偏好色" in call["user"]
    assert "粉色" in call["user"]
