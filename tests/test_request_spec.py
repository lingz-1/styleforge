"""S1 semantic-layer tests: natural language -> RequestSpec equivalence classes.

These lock the *generic* semantic model (operation, action, strength, entity)
rather than any single sentence, so new expressions fall out of the same
vocabulary instead of requiring per-case patches.
"""

from __future__ import annotations

from styleforge.core.request_parser import parse_request
from styleforge.core.request_spec import (
    ChangeAction,
    ConstraintStrength,
    Operation,
    interpret_request,
    to_taskspec,
)


def _find_change(spec, *, action=None, item_type=None, subtype=None):
    for change in spec.changes:
        if action is not None and change.action is not action:
            continue
        if item_type is not None and change.target.item_type != item_type:
            continue
        if subtype is not None and change.target.subtype != subtype:
            continue
        return change
    return None


# ---------------------------------------------------------------------------
# Original regression case.
# ---------------------------------------------------------------------------


def test_no_hat_want_pink_hairclip_parses_to_structured_spec() -> None:
    spec = interpret_request("不要帽子，要粉色系发夹")

    assert spec.operation is Operation.MODIFY
    remove = _find_change(spec, action=ChangeAction.REMOVE, item_type="hats")
    assert remove is not None
    assert remove.strength is ConstraintStrength.MUST_NOT
    assert remove.source_text == "帽子"

    want = _find_change(
        spec, action=ChangeAction.ADD_OR_REPLACE, item_type="hairwear"
    )
    assert want is not None
    assert want.strength is ConstraintStrength.MUST
    assert [(p.value, p.strength) for p in want.preferences] == [
        ("pink", ConstraintStrength.PREFER)
    ]
    assert spec.preferred_colors == ["pink"]


# ---------------------------------------------------------------------------
# Negative + Positive item-type constraints (equivalence class).
# ---------------------------------------------------------------------------


def test_negative_and_positive_type_class() -> None:
    cases = [
        # request, excluded, wanted, want action
        ("不要帽子，要发夹", ("hats", None), ("hairwear", None), ChangeAction.ADD_OR_REPLACE),
        ("不要高跟鞋，要运动鞋", (None, "high_heels"), (None, "sneakers"), ChangeAction.ADD_OR_REPLACE),
        ("不要裙子，要牛仔裤", ("skirt", None), (None, "jeans"), ChangeAction.ADD_OR_REPLACE),
        ("别用耳环，换项链", ("earrings", None), ("necklace", None), ChangeAction.REPLACE),
    ]
    for request, (excl_type, excl_sub), (want_type, want_sub), want_action in cases:
        spec = interpret_request(request)
        assert spec.operation is Operation.MODIFY, request
        remove = _find_change(
            spec, action=ChangeAction.REMOVE,
            item_type=excl_type, subtype=excl_sub,
        )
        assert remove is not None and remove.strength is ConstraintStrength.MUST_NOT, request
        want = _find_change(
            spec, action=want_action,
            item_type=want_type, subtype=want_sub,
        )
        assert want is not None and want.strength is ConstraintStrength.MUST, request


def test_same_sentence_negative_positive_are_distinct_changes() -> None:
    spec = interpret_request("不要帽子，要粉色系发夹")
    assert len(spec.changes) == 2
    assert {c.action for c in spec.changes} == {
        ChangeAction.REMOVE,
        ChangeAction.ADD_OR_REPLACE,
    }


# ---------------------------------------------------------------------------
# Lock + Replace (change-set semantics).
# ---------------------------------------------------------------------------


def test_post_positioned_lock_and_replace() -> None:
    spec = interpret_request("包别动，鞋换运动鞋")

    assert spec.operation is Operation.MODIFY
    assert len(spec.locks) == 1
    assert spec.locks[0].target.item_type == "bag"
    replace = _find_change(spec, action=ChangeAction.REPLACE, subtype="sneakers")
    assert replace is not None
    assert replace.strength is ConstraintStrength.MUST


def test_post_positioned_keep_lock() -> None:
    spec = interpret_request("外套保留，裤子换掉")
    locks = {lock.target.item_type for lock in spec.locks}
    assert "outwear" in locks
    assert any(c.action is ChangeAction.REMOVE for c in spec.changes)


# ---------------------------------------------------------------------------
# Hard vs soft color constraints.
# ---------------------------------------------------------------------------


def test_color_strength_distinguishes_must_prefer() -> None:
    assert interpret_request("必须黑色").color_strength["black"] is ConstraintStrength.MUST
    assert interpret_request("最好黑色").color_strength["black"] is ConstraintStrength.PREFER
    assert interpret_request("想要黑色").color_strength["black"] is ConstraintStrength.PREFER
    assert interpret_request("黑色也可以").color_strength["black"] is ConstraintStrength.PREFER
    assert "red" in interpret_request("不要红色，想要蓝色").excluded_colors


# ---------------------------------------------------------------------------
# Ambiguous anaphora: recorded, never guessed.
# ---------------------------------------------------------------------------


def test_anaphora_is_recorded_not_guessed() -> None:
    spec = interpret_request("把那个换掉")
    assert spec.operation is Operation.MODIFY
    assert "anaphoric_reference" in spec.unresolved_fields


def test_ba_construction_captures_subject_and_skips_constraint() -> None:
    """把这件大衣换成西装 -> REPLACE carries the 大衣 subject; the subject is
    the thing being swapped out, never a recommend-include constraint.  The
    anaphor is still recorded as a fact at parse time (resolution against the
    session outfit is CandidateService's job, not the parser's)."""
    spec = interpret_request("把这件大衣换成西装")
    assert spec.operation is Operation.MODIFY
    change = spec.changes[0]
    assert change.action is ChangeAction.REPLACE
    assert change.target.item_type == "suit"
    assert change.subject is not None
    assert change.subject.item_type == "outwear"
    assert change.subject.subtype == "coat"
    assert spec.constraints == []
    assert "anaphoric_reference" in spec.unresolved_fields


def test_bare_entity_before_replace_is_subject() -> None:
    """外套换件西装 -> the bare entity before 换 is the subject too (not only
    the 把 construction): the current coat is swapped out for a suit."""
    spec = interpret_request("外套换件西装")
    assert len(spec.changes) == 1
    change = spec.changes[0]
    assert change.action is ChangeAction.REPLACE
    assert change.subject is not None
    assert change.subject.item_type == "outwear"
    assert spec.constraints == []


def test_colour_negation_after_entity_is_not_a_subject() -> None:
    """X，不要红色 negates a colour, not the entity: no change may gain the
    entity as a REPLACE subject (the 不要 REMOVE governs a colour, not 鞋)."""
    spec = interpret_request("上衣配半身裙和鞋，不要红色")
    assert all(change.subject is None for change in spec.changes)
    assert "red" in spec.excluded_colors


# ---------------------------------------------------------------------------
# Recommend mainline untouched (TaskSpec projection parity).
# ---------------------------------------------------------------------------


def test_to_taskspec_matches_legacy_parse_request() -> None:
    requests = [
        "给我推荐一套去面试穿的正式穿搭",
        "我要一件黑色的西装外套",
        "帮我搭配一套运动健身穿的",
        "推荐一件粉色连衣裙",
        "需要正式的上班穿搭，衬衫配半身裙和鞋，不要红色。",
        "上衣配西裤和鞋，适合上班。",
    ]
    for request in requests:
        old = parse_request("demo-user", request, 3)
        new = to_taskspec(interpret_request(request), "demo-user", 3)
        assert new.occasion == old.occasion, request
        assert new.required_slots == old.required_slots, request
        assert new.required_item_types_by_slot == old.required_item_types_by_slot, request
        assert new.required_subtypes_by_slot == old.required_subtypes_by_slot, request
        assert new.excluded_subtypes_by_slot == old.excluded_subtypes_by_slot, request
        assert new.excluded_colors == old.excluded_colors, request
        assert new.preferred_colors == old.preferred_colors, request
        assert new.target_audiences == old.target_audiences, request


# ---------------------------------------------------------------------------
# Provenance + single-character guardrails.
# ---------------------------------------------------------------------------


def test_changes_carry_provenance() -> None:
    spec = interpret_request("不要帽子，要发夹")
    remove = _find_change(spec, action=ChangeAction.REMOVE, item_type="hats")
    assert remove.source_text == "帽子"
    assert remove.source_span == (2, 4)
    assert remove.parser_source == "deterministic"
    assert remove.confidence == 1.0


def test_single_char_mention_not_anchored_is_skipped() -> None:
    # "包" inside "包括" must not become a bag mention without an operator.
    spec = interpret_request("推荐一套包括连衣裙的搭配")
    assert spec.operation is Operation.RECOMMEND
    assert all(change.target.item_type != "bag" for change in spec.changes)
