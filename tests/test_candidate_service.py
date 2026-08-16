"""S2 CandidateService tests: constraint-aware retrieval facts (no DB, no LLM).

These lock the *generic* contract of the fact layer that replaces the
target_slot-driven retrieval:

  * REMOVE targets are excluded from the whole candidate pool (not just the
    current outfit).
  * Positive changes drive retrieval independently of any target_slot.
  * Unmentioned current items are kept, not replaced.
  * Feasibility is a reported fact, never a decision.
"""

from __future__ import annotations

from styleforge.core.candidate_service import (
    FeasibilityState,
    build_candidate_pool,
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


# ---------------------------------------------------------------------------
# Original regression case: 不要帽子，要粉色系发夹
# ---------------------------------------------------------------------------


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


def test_no_hat_want_pink_hairclip_facts() -> None:
    spec = interpret_request("不要帽子，要粉色系发夹")
    pool = build_candidate_pool(spec, _make_original_wardrobe(), CURRENT_IDS)

    # Positive change drives retrieval independently of any target_slot.
    assert pool.operation is Operation.MODIFY
    assert pool.feasibility is FeasibilityState.EXACT

    # Hats excluded pool-wide (REMOVE hats applies to the candidate pool too).
    by_id = {i.item_id: i for i in _make_original_wardrobe()}
    assert all(by_id[uid].item_type != "hats" for uid in pool.candidate_item_ids)

    # Hairwear candidate present and pink ranked first.
    clip_ids = pool.add_change_candidates["ADD_OR_REPLACE-0"]
    assert "pink_clip" in clip_ids
    assert "gold_clip" in clip_ids
    assert clip_ids[0] == "pink_clip"

    # Unmentioned current accessories are kept, not replaced: this is the
    # behaviour the old target_slot=accessory path broke (it replaced earrings
    # + rings and hard-locked the purple clip).
    assert "earrings" in pool.keep_ids
    assert "rings" in pool.keep_ids
    assert "purple_clip" in pool.keep_ids
    assert pool.replace_ids == []
    assert pool.lock_ids == []


def test_no_hat_removes_from_pool_even_when_current_has_hat() -> None:
    # Current outfit contains a hat; REMOVE hats must both mark it for
    # replacement AND keep any other hat out of the candidate pool.
    spec = interpret_request("不要帽子")
    wardrobe = _make_original_wardrobe() + [_item("hat3", "hats", name="Cap")]
    current = CURRENT_IDS + ["hat3"]
    pool = build_candidate_pool(spec, wardrobe, current)

    assert "hat3" in pool.replace_ids
    by_id = {i.item_id: i for i in wardrobe}
    assert all(by_id[uid].item_type != "hats" for uid in pool.candidate_item_ids)


# ---------------------------------------------------------------------------
# Lock + replace (change-set semantics).
# ---------------------------------------------------------------------------


def test_lock_and_replace_partition_current() -> None:
    spec = interpret_request("包别动，鞋换运动鞋")
    wardrobe = _make_original_wardrobe() + [
        _item("sneaker", "shoes", name="White sneakers", color="white"),
    ]
    pool = build_candidate_pool(spec, wardrobe, CURRENT_IDS)

    assert "bag" in pool.lock_ids
    assert "shoes" in pool.replace_ids
    assert "dress" in pool.keep_ids
    assert "sneaker" in pool.candidate_item_ids
    assert pool.feasibility is FeasibilityState.EXACT


# ---------------------------------------------------------------------------
# Feasibility facts.
# ---------------------------------------------------------------------------


def test_unmet_must_change_is_unsatisfiable() -> None:
    spec = _modify_spec(
        [_change(ChangeAction.ADD_OR_REPLACE, item_type="hairwear", source_text="发夹")]
    )
    wardrobe = [  # No hairwear of any kind.
        _item("dress", "dress", name="Dress"),
        _item("hat1", "hats", name="Hat"),
    ]
    pool = build_candidate_pool(spec, wardrobe, ["dress"])

    assert pool.feasibility is FeasibilityState.UNSATISFIABLE
    assert "发夹" in pool.unmet_constraints
    assert pool.add_change_candidates["ADD_OR_REPLACE-0"] == []


def test_must_color_without_that_color_is_relaxable() -> None:
    # MUST pink hair clip, but the only clips are gold: exact is empty, relaxed
    # (colour dropped) still has candidates -> SATISFIABLE_WITH_RELAXATION.
    spec = _modify_spec(
        [
            Change(
                action=ChangeAction.ADD_OR_REPLACE,
                target=EntityRef(item_type="hairwear", subtype="hair_clip"),
                strength=ConstraintStrength.MUST,
                preferences=[
                    {
                        "attribute": "color_family",
                        "value": "pink",
                        "strength": ConstraintStrength.MUST,
                    }
                ],
            )
        ]
    )
    wardrobe = [
        _item("gold_clip", "hairwear", name="Gold barrette", color="gold",
              description="", features=("hair clip",)),
    ]
    pool = build_candidate_pool(spec, wardrobe, ["dress"])

    assert pool.feasibility is FeasibilityState.SATISFIABLE_WITH_RELAXATION
    cov = pool.coverage[0]
    assert cov.exact_ids == []
    assert "gold_clip" in cov.relaxed_ids
    # Pool still surfaces the relaxed option so Agent 2 can decide to relax.
    assert "gold_clip" in pool.candidate_item_ids


def test_prefer_color_does_not_gate() -> None:
    # PREFER pink, only gold available: no relaxation needed, gold is a valid
    # exact candidate (PREFER never blocks).
    spec = _modify_spec(
        [
            Change(
                action=ChangeAction.ADD_OR_REPLACE,
                target=EntityRef(item_type="hairwear"),
                strength=ConstraintStrength.MUST,
                preferences=[
                    {
                        "attribute": "color_family",
                        "value": "pink",
                        "strength": ConstraintStrength.PREFER,
                    }
                ],
            )
        ]
    )
    wardrobe = [_item("gold_clip", "hairwear", name="Gold barrette", color="gold")]
    pool = build_candidate_pool(spec, wardrobe, ["dress"])

    assert pool.feasibility is FeasibilityState.EXACT
    assert "gold_clip" in pool.add_change_candidates["ADD_OR_REPLACE-0"]


def test_only_remove_or_lock_is_exact() -> None:
    spec = _modify_spec([_change(ChangeAction.REMOVE, item_type="hats", source_text="帽子")])
    pool = build_candidate_pool(spec, _make_original_wardrobe(), CURRENT_IDS)
    assert pool.feasibility is FeasibilityState.EXACT
    assert pool.coverage == []


def test_anaphora_is_needs_clarification() -> None:
    spec = interpret_request("把那个换掉")
    pool = build_candidate_pool(spec, _make_original_wardrobe(), CURRENT_IDS)
    assert pool.feasibility is FeasibilityState.NEEDS_CLARIFICATION


def test_ba_subject_anchors_replace_to_current_item() -> None:
    """"把这件大衣换成西装" -> the REPLACE subject (大衣) names the current
    coat, so it is marked replaced and the anaphor resolves in-session (EXACT,
    no clarification).  The suit target surfaces a blazer via free-text anchor."""
    wardrobe = _make_original_wardrobe() + [
        _item("coat", "outwear", name="灰色大衣", color="gray"),
        _item("blazer", "outwear", name="深蓝西装外套", color="navy"),
    ]
    current = ["dress", "shoes", "coat"]
    spec = interpret_request("把这件大衣换成西装")
    pool = build_candidate_pool(spec, wardrobe, current)

    assert "coat" in pool.replace_ids
    assert "dress" in pool.keep_ids
    assert pool.feasibility is FeasibilityState.EXACT
    assert "blazer" in pool.candidate_item_ids


def test_entity_before_replace_is_subject_not_constraint() -> None:
    """外套换件西装 -> 外套 is the subject of the swap (the current coat going
    out), not an include preference: the REPLACE gets the subject and no bogus
    outwear constraint is emitted."""
    spec = interpret_request("外套换件西装")
    assert len(spec.changes) == 1
    change = spec.changes[0]
    assert change.action is ChangeAction.REPLACE
    assert change.subject is not None
    assert change.subject.item_type == "outwear"
    assert all(c.target.item_type != "outwear" for c in spec.constraints)


def test_bounded_pool_excludes_current_items() -> None:
    spec = interpret_request("要发夹")
    pool = build_candidate_pool(spec, _make_original_wardrobe(), CURRENT_IDS)
    assert not set(pool.candidate_item_ids) & set(CURRENT_IDS)


def test_missing_target_slot_reports_unsatisfiable_not_clarification() -> None:
    """"把外套换成白色短西装的" with no suit in the wardrobe.

    The old behaviour free-rebuilt the outfit to gain the missing slot; the
    structured behaviour reports the missing target as a fact (UNSATISFIABLE)
    and does not bounce back with a clarification.  Whether to relax or ask is
    Agent 2's decision (PR3/PR4), not a fact-layer one.
    """
    seed = [
        _item("shirt", "top", name="White collared shirt", color="white"),
        _item("trousers", "pants", name="Black tailored trousers", color="black"),
        _item("loafers", "shoes", name="Black leather loafers", color="black"),
        _item("trench", "outwear", name="Beige trench coat", color="beige"),
    ]
    spec = interpret_request("把外套换成白色短西装的")
    assert spec.changes, "REPLACE suit must parse as a structured change"
    pool = build_candidate_pool(spec, seed, ["shirt", "trousers", "loafers"])

    assert pool.feasibility is FeasibilityState.UNSATISFIABLE
    assert pool.unmet_constraints == ["西装"]
    # Not a clarification: the intent is grounded, only the target is absent.
    assert pool.coverage[0].all_target_ids == []


def test_excluded_color_keeps_out_of_pool() -> None:
    spec = _modify_spec(
        [_change(ChangeAction.ADD_OR_REPLACE, item_type="hairwear", source_text="发夹")],
        excluded_colors=["pink"],
    )
    wardrobe = [
        _item("pink_clip", "hairwear", name="Pink barrette", color="pink",
              description="", features=("hair clip",)),
        _item("gold_clip", "hairwear", name="Gold barrette", color="gold",
              description="", features=("hair clip",)),
    ]
    pool = build_candidate_pool(spec, wardrobe, ["dress"])

    assert "pink_clip" not in pool.candidate_item_ids
    assert "gold_clip" in pool.candidate_item_ids
    assert pool.excluded_colors == ["pink"]


def test_prefer_color_gap_reported_pink_not_must_relaxed() -> None:
    """Pink is a PREFER, never a MUST: with no pink hair clip in the wardrobe,
    the relaxation plan reports the preference gap explicitly (unmet_prefer_colors
    + a drop-PREFER level) while the hairwear MUST stays exact and the MUST_NOT
    hat exclusion never lets a hat back in.  This is the original acceptance
    case's fact layer: Agent 2 may relax *only* pink, never hairwear."""
    wardrobe = [
        _item("dress", "dress", name="Little black dress", color="black"),
        _item("shoes", "shoes", name="Black heels", color="black"),
        _item("purple_clip", "other", name="Purple hair clip", color="purple",
              description="", features=("hair clip",)),
        _item("hat1", "hats", name="Fedora", color="brown"),
        # No pink clip: the PREFER colour is unsatisfiable at every level.
        _item("gold_clip", "hairwear", name="Gold barrette", color="gold",
              description="", features=("hair clip",)),
        # Another accessory, reachable only at the type-broadening level.
        _item("necklace", "necklace", name="Gold necklace", color="gold"),
    ]
    current = ["dress", "shoes", "purple_clip"]
    spec = interpret_request("不要帽子，要粉色系发夹")
    pool = build_candidate_pool(spec, wardrobe, current)
    plan = build_relaxation_plan(pool, spec, wardrobe)

    assert pool.feasibility is FeasibilityState.EXACT
    option = next(o for o in plan.options if o.change_id == "ADD_OR_REPLACE-0")
    # The gap is reported, not silent.
    assert option.unmet_prefer_colors == ["pink"]
    levels = {level.level: level for level in option.chain}
    # L0 exact keeps the hairwear MUST; L1 drops only the pink preference;
    # L3 broadens to the accessory slot.  No hat anywhere.
    assert levels[0].kind == "exact"
    assert levels[0].candidate_ids == ["gold_clip"]
    assert levels[1].kind == "color"
    assert "粉色" in levels[1].dropped
    assert levels[1].candidate_ids == ["gold_clip"]
    assert option.minimal_level == 0
    assert {level.level for level in option.chain} == {0, 1, 3}
    assert "pink" in pool.excluded_colors or not any(
        item.item_type == "hats"
        for item in wardrobe
        if item.item_id in pool.candidate_item_ids
    )
    assert "hat1" not in pool.candidate_item_ids
