"""Constraint-aware candidate retrieval: RequestSpec -> grounded candidate facts.

Layer boundary (S2 of the semantic rework):
  CandidateService is a pure, deterministic, database-free layer.  It consumes
  a ``RequestSpec`` (the semantic source of truth) plus the wardrobe and the
  current outfit, and returns *facts*: which current items are locked, replaced
  or kept; which wardrobe items satisfy each positive change (exact vs relaxed);
  which entity types are excluded from the whole pool; and a feasibility state
  describing whether every MUST constraint is achievable.

  It never decides.  It does not pick an outfit, does not relax anything by
  itself, does not fall back to a target_slot when a constraint is ambiguous.
  The decision power stays with Agent 2; the relaxation options stay a reported
  fact (PR3 turns them into a policy).  There is deliberately no per-case
  branching here -- only generic entity/strength/action vocabulary.

  Key contract vs the old behaviour:
    * REMOVE targets (负向约束) are applied to the *whole* candidate pool, not
      just to the current outfit.  "不要帽子" means no hat may enter any
      replacement slot either.
    * Positive changes drive retrieval independently of ``target_slot``.  The
      router may still extract a target_slot, but it no longer decides what is
      searched or what is locked.
    * Current items that no change mentions are kept (not replaced) -- the
      user's change set is the full source of truth for what to touch.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from styleforge.core.categories import infer_slot
from styleforge.core.garment_attributes import SUBTYPE_RULES as _SUBTYPE_RULES
from styleforge.core.garment_attributes import item_matches_subtype
from styleforge.core.request_parser import COLOR_ALIASES
from styleforge.core.request_spec import (
    Change,
    ChangeAction,
    ConstraintStrength,
    EntityRef,
    Operation,
    RequestSpec,
)
from styleforge.core.schemas import CatalogItem


class FeasibilityState(str, Enum):
    EXACT = "EXACT"                                # every MUST is achievable as-is
    SATISFIABLE_WITH_RELAXATION = "SATISFIABLE_WITH_RELAXATION"  # MUST needs colour/type relaxation
    UNSATISFIABLE = "UNSATISFIABLE"                # a MUST has no candidate at any level
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"    # intent cannot be grounded without a question


_POSITIVE_ACTIONS = (ChangeAction.ADD, ChangeAction.ADD_OR_REPLACE, ChangeAction.REPLACE)
_DEFAULT_CANDIDATE_LIMIT = 120
_DEFAULT_PER_CHANGE_LIMIT = 10


class ConstraintCoverage(BaseModel):
    """Per-change candidate facts (never a decision)."""

    change_id: str
    action: ChangeAction
    strength: ConstraintStrength
    source_text: str = ""
    target: EntityRef = Field(default_factory=EntityRef)
    # exact_ids: items that hit the target AND every MUST colour preference.
    exact_ids: list[str] = Field(default_factory=list)
    # relaxed_ids: target hits, MUST colours dropped (same type/subtype/slot).
    relaxed_ids: list[str] = Field(default_factory=list)
    # all_target_ids: every target hit regardless of colour (relaxation ceiling).
    all_target_ids: list[str] = Field(default_factory=list)
    # prefer_missed_ids: exact candidates that miss >=1 PREFER colour.  These
    # stay the answer when the preference is dropped -- the cheapest relaxation,
    # because the MUST target/colour gate is untouched.
    prefer_missed_ids: list[str] = Field(default_factory=list)
    # unmet_prefer_colors: PREFER colours no all_target item carries at all,
    # i.e. the preference is unsatisfiable at every relaxation level.
    unmet_prefer_colors: list[str] = Field(default_factory=list)

    @property
    def exact(self) -> bool:
        return bool(self.exact_ids)

    @property
    def relaxed_available(self) -> bool:
        return bool(self.relaxed_ids)

    @property
    def unmet(self) -> bool:
        """No candidate even at the widest relaxation level."""
        return not self.all_target_ids


class CandidatePool(BaseModel):
    """The full factual retrieval result for one modify request."""

    operation: Operation
    current_ids: list[str] = Field(default_factory=list)
    # Partition of the current outfit under the user's change set.
    lock_ids: list[str] = Field(default_factory=list)      # user explicitly locked
    replace_ids: list[str] = Field(default_factory=list)   # user wants changed/removed
    keep_ids: list[str] = Field(default_factory=list)      # unmentioned -> kept
    # change_id -> candidate UUIDs for ADD / ADD_OR_REPLACE / REPLACE.
    add_change_candidates: dict[str, list[str]] = Field(default_factory=dict)
    # REMOVE targets, applied to the whole pool (as (item_type, subtype) pairs).
    excluded_type_subtypes: list[dict[str, str | None]] = Field(default_factory=list)
    excluded_colors: list[str] = Field(default_factory=list)
    coverage: list[ConstraintCoverage] = Field(default_factory=list)
    feasibility: FeasibilityState = FeasibilityState.EXACT
    unmet_constraints: list[str] = Field(default_factory=list)
    # The bounded pool handed to Agent 2 (per-change candidates first, then a
    # slot-balanced fill so a rebuild is never starved).
    candidate_item_ids: list[str] = Field(default_factory=list)


def _item_text(item: CatalogItem) -> str:
    return f"{item.name} {item.description} {' '.join(item.features)}".lower()


def _strict_target_match(item: CatalogItem, target: EntityRef) -> bool:
    """Precise match on item_type / subtype / slot only (no free text).

    Used for REMOVE exclusions so a bare word inside a description cannot
    accidentally exclude an unrelated item.  The slot dimension matches ONLY
    when the target carries no more-specific dimension: "发夹" parses to
    item_type=hairwear + slot=accessory, and must match a hair clip, not every
    accessory in the slot.
    """
    if target.item_type and item.item_type == target.item_type:
        return True
    if target.subtype:
        try:
            if item_matches_subtype(item, target.subtype):
                return True
        except ValueError:
            pass
    if target.slot and not target.item_type and not target.subtype:
        if infer_slot(item.item_type) == target.slot:
            return True
    return False


def _lenient_target_match(
    item: CatalogItem, target: EntityRef, source_text: str = ""
) -> bool:
    """Precise match, plus free-text anchoring of the user's own words.

    Needed because real catalogs type some items loosely (a purple hair clip may
    be item_type=other), so "发夹" must still anchor to an item whose name or
    description carries the word.  Only multi-char source words anchor text;
    single chars ("帽") are too noisy.
    """
    if _strict_target_match(item, target):
        return True
    if source_text and len(source_text) >= 2 and source_text in _item_text(item):
        return True
    return False


def _item_has_color(item: CatalogItem, color: str) -> bool:
    aliases = COLOR_ALIASES.get(color, (color,))
    low = item.color.strip().lower()
    return any(alias in low for alias in aliases) if aliases else color in low


def _change_pref_colors(change: Change) -> tuple[list[str], list[str]]:
    """(must_colors, prefer_colors) declared directly on the change."""
    must: list[str] = []
    prefer: list[str] = []
    for pref in change.preferences:
        if pref.attribute != "color_family":
            continue
        if pref.strength is ConstraintStrength.MUST:
            must.append(pref.value)
        else:
            prefer.append(pref.value)
    return must, prefer


def _score_candidate(
    item: CatalogItem, must_colors: list[str], prefer_colors: list[str]
) -> float:
    """Higher is better.  MUST colours gate entry; PREFER colours rank."""
    score = 0.0
    score += 25.0 * sum(1 for c in must_colors if _item_has_color(item, c))
    score += 10.0 * sum(1 for c in prefer_colors if _item_has_color(item, c))
    return score


def _build_coverage(
    change: Change,
    change_index: int,
    wardrobe: list[CatalogItem],
    excluded_ids: set[str],
) -> ConstraintCoverage:
    must_colors, prefer_colors = _change_pref_colors(change)
    all_target: list[CatalogItem] = [
        item
        for item in wardrobe
        if item.item_id not in excluded_ids
        and _lenient_target_match(item, change.target, change.source_text)
    ]
    exact: list[CatalogItem] = []
    relaxed: list[CatalogItem] = []
    for item in all_target:
        if all(_item_has_color(item, color) for color in must_colors):
            exact.append(item)
        else:
            relaxed.append(item)
    ranked_exact = sorted(
        exact, key=lambda i: (-_score_candidate(i, must_colors, prefer_colors), i.item_id)
    )
    ranked_relaxed = sorted(
        relaxed, key=lambda i: (-_score_candidate(i, [], prefer_colors), i.item_id)
    )
    change_id = f"{change.action.value}-{change_index}"
    # Exact candidates that miss >=1 PREFER colour: what stays available when the
    # preference is dropped (a soft-colour relaxation, MUST gate untouched).
    prefer_missed = [
        i.item_id
        for i in exact
        if not all(_item_has_color(i, color) for color in prefer_colors)
    ]
    # PREFER colours that no target hit carries at all: unsatisfiable at any
    # relaxation level, surfaced so the gap is explicit rather than silent.
    unmet_prefer_colors = [
        color
        for color in prefer_colors
        if not any(_item_has_color(item, color) for item in all_target)
    ]
    return ConstraintCoverage(
        change_id=change_id,
        action=change.action,
        strength=change.strength,
        source_text=change.source_text,
        target=change.target,
        exact_ids=[i.item_id for i in ranked_exact],
        relaxed_ids=[i.item_id for i in ranked_relaxed],
        all_target_ids=[i.item_id for i in all_target],
        prefer_missed_ids=prefer_missed,
        unmet_prefer_colors=unmet_prefer_colors,
    )


def _subject_anaphor_resolved(
    spec: RequestSpec,
    wardrobe_by_id: dict[str, CatalogItem],
    current_ids: list[str],
) -> bool:
    """Whether the spec's anaphor is anchored to a concrete current item.

    "把这件大衣换成西装" carries 这件 + subject 大衣; inside a session where
    the current outfit holds a coat, the anaphor is resolved — no clarification
    is needed.  Only REPLACE subjects count: their whole point is naming the
    current item being swapped out.  REMOVE/ADD subjects carry no such anchor
    obligation, and a bare "把那个换掉" (no subject entity) stays unresolved.
    """
    if "anaphoric_reference" not in spec.unresolved_fields:
        return True
    for change in spec.changes:
        if change.action is not ChangeAction.REPLACE or change.subject is None:
            continue
        if any(
            wardrobe_by_id.get(uid) is not None
            and _strict_target_match(wardrobe_by_id[uid], change.subject)
            for uid in current_ids
        ):
            return True
    return False


def _assess_feasibility(
    coverage: list[ConstraintCoverage],
    spec: RequestSpec,
    *,
    anaphor_resolved: bool = False,
) -> tuple[FeasibilityState, list[str]]:
    positive = [c for c in coverage if c.action in _POSITIVE_ACTIONS]
    must = [c for c in positive if c.strength is ConstraintStrength.MUST]
    if "anaphoric_reference" in spec.unresolved_fields and not anaphor_resolved:
        return FeasibilityState.NEEDS_CLARIFICATION, []
    if not positive:
        # Only REMOVE / LOCK changes: the exclusions are satisfiable facts.
        return FeasibilityState.EXACT, []
    unmet = [c for c in must if c.unmet]
    if unmet:
        return FeasibilityState.UNSATISFIABLE, [c.source_text for c in unmet]
    if any(not c.exact and c.relaxed_available for c in must):
        return FeasibilityState.SATISFIABLE_WITH_RELAXATION, []
    return FeasibilityState.EXACT, []


def _derived_slot(target: EntityRef) -> str:
    """Slot a REPLACE target points at, so current items in that slot are replaced."""
    if target.slot:
        return target.slot
    if target.item_type:
        return infer_slot(target.item_type)
    if target.subtype:
        rule = _SUBTYPE_RULES.get(target.subtype)
        if rule is not None and rule.item_types:
            return infer_slot(rule.item_types[0])
    return ""


def _replace_matches_current(item: CatalogItem, target: EntityRef) -> bool:
    """Whether a current item is the object of a REPLACE change.

    REPLACE is directional: "鞋换运动鞋" means the current footwear is replaced
    by a sneaker, so the *current* item matches when its slot equals the slot
    the replacement target resolves to.  REMOVE stays precise (only items the
    target names are replaced); REPLACE additionally replaces the target's slot.
    """
    if _strict_target_match(item, target):
        return True
    slot = _derived_slot(target)
    return bool(slot) and infer_slot(item.item_type) == slot


def _partition_current(
    spec: RequestSpec,
    current_ids: list[str],
    wardrobe_by_id: dict[str, CatalogItem],
) -> tuple[list[str], list[str], list[str]]:
    """Classify each current item as locked / replaced / kept under the change set."""
    lock_ids: list[str] = []
    replace_ids: list[str] = []
    keep_ids: list[str] = []
    for uid in current_ids:
        item = wardrobe_by_id.get(uid)
        if item is None:
            continue
        locked = any(
            lock.target is not None
            and _lenient_target_match(item, lock.target, lock.source_text)
            for lock in spec.locks
        )
        if locked:
            lock_ids.append(uid)
            continue
        replaced = any(
            change.action is ChangeAction.REMOVE
            and _strict_target_match(item, change.target)
            or change.action is ChangeAction.REPLACE
            and (
                # "把这件大衣换成西装" -> REPLACE target=西装 subject=大衣: the
                # subject names the current item actually being swapped out, so
                # it alone decides the replacement (no derived-slot fallback,
                # which would over-match e.g. a dress via suit->one_piece).
                change.subject is not None
                and _strict_target_match(item, change.subject)
                or change.subject is None
                and _replace_matches_current(item, change.target)
            )
            for change in spec.changes
        )
        if replaced:
            replace_ids.append(uid)
        else:
            keep_ids.append(uid)
    return lock_ids, replace_ids, keep_ids


def _excluded_entities(spec: RequestSpec) -> list[dict[str, str | None]]:
    """REMOVE targets as (item_type, subtype) pairs applied pool-wide."""
    result: list[dict[str, str | None]] = []
    for change in spec.changes:
        if change.action is ChangeAction.REMOVE:
            result.append(
                {
                    "item_type": change.target.item_type,
                    "subtype": change.target.subtype,
                }
            )
    return result


def _item_excluded(item: CatalogItem, excluded: list[dict[str, str | None]]) -> bool:
    for entry in excluded:
        if entry["item_type"] and item.item_type == entry["item_type"]:
            return True
        if entry["subtype"]:
            try:
                if item_matches_subtype(item, entry["subtype"]):
                    return True
            except ValueError:
                pass
    return False


def _fill_slot_balanced(
    wardrobe: list[CatalogItem],
    excluded_ids: set[str],
    chosen: list[str],
    *,
    limit: int,
) -> list[str]:
    """Append a slot-balanced fill of the remaining wardrobe up to ``limit``."""
    seen: set[str] = set(chosen)
    by_slot: dict[str, list[str]] = {}
    for item in wardrobe:
        if item.item_id in seen or item.item_id in excluded_ids:
            continue
        by_slot.setdefault(infer_slot(item.item_type), []).append(item.item_id)
    slots = list(by_slot)
    while len(chosen) < limit and slots:
        for slot in list(slots):
            bucket = by_slot[slot]
            if not bucket:
                slots.remove(slot)
                continue
            chosen.append(bucket.pop(0))
            if len(chosen) >= limit:
                break
    return chosen


def build_candidate_pool(
    spec: RequestSpec,
    wardrobe: list[CatalogItem],
    current_ids: list[str],
    *,
    candidate_limit: int = _DEFAULT_CANDIDATE_LIMIT,
    per_change_limit: int = _DEFAULT_PER_CHANGE_LIMIT,
) -> CandidatePool:
    """Build the factual candidate pool for a modify RequestSpec.

    Pure function: no I/O, no LLM, no mutation.  Every MUST positive change gets
    its own exact/relaxed coverage; REMOVE targets are excluded from the whole
    pool; the current outfit is partitioned into lock/replace/keep.
    """
    wardrobe_by_id = {item.item_id: item for item in wardrobe}
    current_set = set(current_ids)
    lock_ids, replace_ids, keep_ids = _partition_current(
        spec, current_ids, wardrobe_by_id
    )

    excluded_entities = _excluded_entities(spec)
    excluded_ids: set[str] = {
        item.item_id
        for item in wardrobe
        if _item_excluded(item, excluded_entities)
        or any(item.color and _item_has_color(item, color) for color in spec.excluded_colors)
    }
    # Candidates never come from the current outfit; Agent 2 sees current items
    # separately as current_item_ids.
    excluded_ids |= current_set

    positive_changes = [
        change for change in spec.changes if change.action in _POSITIVE_ACTIONS
    ]
    coverage: list[ConstraintCoverage] = []
    add_change_candidates: dict[str, list[str]] = {}
    for change_index, change in enumerate(positive_changes):
        cov = _build_coverage(change, change_index, wardrobe, excluded_ids)
        coverage.append(cov)
        pool_for_change = (
            cov.exact_ids if cov.exact_ids else cov.relaxed_ids
        )[:per_change_limit]
        add_change_candidates[cov.change_id] = pool_for_change

    anaphor_resolved = _subject_anaphor_resolved(spec, wardrobe_by_id, current_ids)
    feasibility, unmet = _assess_feasibility(
        coverage, spec, anaphor_resolved=anaphor_resolved
    )

    # Order the bounded pool: per-change candidates first (in change order),
    # then a slot-balanced fill of the rest so a full rebuild is never starved.
    ordered: list[str] = []
    seen: set[str] = set()
    for change_id in add_change_candidates:
        for uid in add_change_candidates[change_id]:
            if uid in seen:
                continue
            seen.add(uid)
            ordered.append(uid)
    candidate_item_ids = _fill_slot_balanced(
        wardrobe, excluded_ids, ordered, limit=candidate_limit
    )

    return CandidatePool(
        operation=spec.operation,
        current_ids=list(current_ids),
        lock_ids=lock_ids,
        replace_ids=replace_ids,
        keep_ids=keep_ids,
        add_change_candidates=add_change_candidates,
        excluded_type_subtypes=excluded_entities,
        excluded_colors=list(spec.excluded_colors),
        coverage=coverage,
        feasibility=feasibility,
        unmet_constraints=unmet,
        candidate_item_ids=candidate_item_ids,
    )
