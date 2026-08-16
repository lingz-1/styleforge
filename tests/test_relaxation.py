"""S3 relaxation-policy tests: MUST constraints -> generic relaxation chains.

The chain (exact -> drop PREFER -> drop MUST colour -> broaden type) is ordered
by constraint strength: PREFER colours go first, MUST colours second, the item
type broadens last.  It is factual: it lists what is given up and what each
level unlocks, never deciding.  Two invariants are locked here: REMOVE
exclusions survive every level (a "不要帽子" must never let a hat back in via a
type-broadening step), and a level only ever ADDS candidates previously
filtered out.
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


def _pink_clip() -> CatalogItem:
    return _item("pink_clip", "hairwear", name="Pink barrette", color="pink",
                 description="", features=("hair clip",))


def _gold_clip() -> CatalogItem:
    return _item("gold_clip", "hairwear", name="Gold barrette", color="gold",
                 description="", features=("hair clip",))


def _hat() -> CatalogItem:
    return _item("hat1", "hats", name="Fedora", color="brown")


def _earrings() -> CatalogItem:
    return _item("earrings", "earrings", name="Stud earrings", color="gold")


def _chain(plan, change_id: str):
    option = next(opt for opt in plan.options if opt.change_id == change_id)
    return option


def test_exact_change_gets_level0_and_type_broaden() -> None:
    spec = interpret_request("不要帽子，要发夹")
    wardrobe = [_pink_clip(), _gold_clip(), _hat(), _earrings()]
    pool = build_candidate_pool(spec, wardrobe, [])
    plan = build_relaxation_plan(pool, spec, wardrobe)

    assert plan.feasibility is FeasibilityState.EXACT
    option = _chain(plan, "ADD_OR_REPLACE-0")
    assert option.exact_count == 2
    assert option.minimal_level == 0
    levels = {level.level: level for level in option.chain}
    # Level 3 broadens to the accessory slot, but hats (REMOVE-excluded) never
    # come back in.
    assert "earrings" in levels[3].candidate_ids
    assert "hat1" not in levels[3].candidate_ids


def test_must_color_relaxation_marks_level1_as_minimal() -> None:
    spec = RequestSpec(
        operation=Operation.MODIFY,
        changes=[
            Change(
                action=ChangeAction.ADD_OR_REPLACE,
                target=EntityRef(item_type="hairwear"),
                strength=ConstraintStrength.MUST,
                preferences=[
                    {
                        "attribute": "color_family",
                        "value": "pink",
                        "strength": ConstraintStrength.MUST,
                    }
                ],
            )
        ],
    )
    wardrobe = [_gold_clip(), _earrings()]
    pool = build_candidate_pool(spec, wardrobe, [])
    plan = build_relaxation_plan(pool, spec, wardrobe)

    assert plan.feasibility is FeasibilityState.SATISFIABLE_WITH_RELAXATION
    option = _chain(plan, "ADD_OR_REPLACE-0")
    levels = {level.level: level for level in option.chain}
    assert option.exact_count == 0
    # The pink colour is MUST here (not PREFER), so it is dropped only at the
    # MUST-colour level 2; gold_clip re-enters there.
    assert "gold_clip" in levels[2].candidate_ids
    assert "粉色" in levels[2].dropped
    assert option.minimal_level == 2


def test_unsatisfiable_target_broadens_to_slot() -> None:
    # No hairwear at all, but the accessory slot has earrings.
    spec = interpret_request("要发夹")
    wardrobe = [_earrings()]
    pool = build_candidate_pool(spec, wardrobe, [])
    plan = build_relaxation_plan(pool, spec, wardrobe)

    assert plan.feasibility is FeasibilityState.UNSATISFIABLE
    option = _chain(plan, "ADD_OR_REPLACE-0")
    assert option.exact_count == 0
    assert option.unmet is False          # level 3 still offers a choice
    assert option.minimal_level == 3
    assert "earrings" in option.chain[-1].candidate_ids


def test_fully_unmet_change_has_empty_chain() -> None:
    # Wardrobe has only a hat (excluded by REMOVE) and a dress; the accessory
    # slot is empty, so even the type-broadening level has nothing.
    spec = interpret_request("不要帽子，要发夹")
    wardrobe = [_hat(), _item("dress", "dress", name="Dress")]
    pool = build_candidate_pool(spec, wardrobe, [])
    plan = build_relaxation_plan(pool, spec, wardrobe)

    option = _chain(plan, "ADD_OR_REPLACE-0")
    assert option.unmet is True
    assert option.minimal_level is None
    assert all(not level.candidate_ids for level in option.chain)
