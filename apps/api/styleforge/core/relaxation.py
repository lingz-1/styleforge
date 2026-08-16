"""Generic relaxation policy: MUST-constraint facts -> a bounded relaxation chain.

Layer boundary (S3 of the semantic rework):
  ``CandidateService`` reports whether each positive change is exactly
  satisfiable.  ``RelaxationPolicy`` turns those facts into a *generic*
  relaxation chain for every MUST positive change, each level carrying the
  candidate ids it would unlock and what it gives up.

  Relaxation is ordered by constraint strength, never by guesswork:
    * MUST / MUST_NOT (REMOVE) are never relaxed -- REMOVE exclusions stay in
      force at every level, and a MUST change only ever *unlocks more of its
      own target* (never swaps its type away wholesale).
    * PREFER colours are the softest thing on a MUST change, so they are the
      first thing dropped (level 1).  Then MUST colours (level 2).  Then the
      item type broadens to the target's slot (level 3).  color -> type -> slot
      is a priority among the relaxable dimensions, not a license to drop the
      MUST target itself.

  Two invariants:
    * REMOVE exclusions stay in force at every relaxation level.  "不要帽子"
      never lets a hat back into the pool, no matter how starved the target is.
    * Level 0 is the exact answer; later levels add candidates previously
      filtered out by colour or by item-type specificity (the drop-PREFER
      level is the one reporting level: its candidates sit inside exact, and
      its job is to make the surrendered preference explicit, not to unlock).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from styleforge.core.candidate_service import (
    _POSITIVE_ACTIONS,
    _item_excluded,
    _item_has_color,
    CandidatePool,
    FeasibilityState,
)
from styleforge.core.categories import infer_slot
from styleforge.core.garment_attributes import SUBTYPE_RULES, item_matches_subtype
from styleforge.core.request_parser import COLOR_ALIASES
from styleforge.core.request_spec import (
    Change,
    ConstraintStrength,
    EntityRef,
    RequestSpec,
)
from styleforge.core.schemas import CatalogItem


class RelaxationLevel(BaseModel):
    """One step of a relaxation chain, with the candidates it unlocks."""

    level: int
    dropped: str = ""            # human label of what was given up ("粉色")
    kind: str = "exact"          # exact | color | type
    candidate_ids: list[str] = Field(default_factory=list)
    description: str = ""


class RelaxationOption(BaseModel):
    """The full relaxation chain for one MUST positive change."""

    change_id: str
    source_text: str = ""
    target: EntityRef = Field(default_factory=EntityRef)
    exact_count: int = 0
    chain: list[RelaxationLevel] = Field(default_factory=list)
    minimal_level: int | None = None   # first chain level with candidates
    unmet: bool = False                # no candidate at any level
    # PREFER colours no candidate carries at all -- an explicit, reportable
    # gap that Agent 2 may choose to accept (RELAX_PREFERENCE) or not.
    unmet_prefer_colors: list[str] = Field(default_factory=list)


class RelaxationPlan(BaseModel):
    feasibility: FeasibilityState
    options: list[RelaxationOption] = Field(default_factory=list)


def _excluded_ids(pool: CandidatePool, wardrobe: list[CatalogItem]) -> set[str]:
    return {
        item.item_id
        for item in wardrobe
        if _item_excluded(item, pool.excluded_type_subtypes)
        or any(
            item.color and _item_has_color(item, color)
            for color in pool.excluded_colors
        )
    }


def _must_colors(change: Change) -> list[str]:
    return [
        pref.value
        for pref in change.preferences
        if pref.attribute == "color_family"
        and pref.strength is ConstraintStrength.MUST
    ]


def _prefer_colors(change: Change) -> list[str]:
    return [
        pref.value
        for pref in change.preferences
        if pref.attribute == "color_family"
        and pref.strength is ConstraintStrength.PREFER
    ]


def _color_label(color: str) -> str:
    """Human-readable colour label (粉色, not the canonical pink)."""
    aliases = COLOR_ALIASES.get(color)
    return aliases[0] if aliases else color


def _target_slot(target: EntityRef) -> str:
    """Slot a change target resolves to, for the type-broadening level."""
    if target.slot:
        return target.slot
    if target.item_type:
        return infer_slot(target.item_type)
    if target.subtype:
        rule = SUBTYPE_RULES.get(target.subtype)
        if rule is not None and rule.item_types:
            return infer_slot(rule.item_types[0])
    return ""


def _change_matches_type(item: CatalogItem, target: EntityRef) -> bool:
    """Broad-type match used for the type-broadening level.

    ``hairwear`` broadens to the accessory slot (other accessory items) when no
    hair clip exists -- but never to a hat, because REMOVE exclusions stay in
    force at every level.  The slot dimension matches only when the target is
    no more specific than a slot (same rule as CandidateService).
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


def build_relaxation_plan(
    pool: CandidatePool,
    spec: RequestSpec,
    wardrobe: list[CatalogItem],
) -> RelaxationPlan:
    """Derive the relaxation chain for every MUST positive change.

    Facts only: each level lists what is dropped and which candidates it
    unlocks; ``minimal_level`` marks the cheapest satisfiable step.  Agent 2's
    decision policy consumes this (PR4).
    """
    excluded = _excluded_ids(pool, wardrobe)
    options: list[RelaxationOption] = []
    positive_changes = [
        change for change in spec.changes if change.action in _POSITIVE_ACTIONS
    ]
    coverage_by_id = {cov.change_id: cov for cov in pool.coverage}
    for index, change in enumerate(positive_changes):
        if change.strength is not ConstraintStrength.MUST:
            continue
        cov = coverage_by_id.get(f"{change.action.value}-{index}")
        if cov is None:
            continue
        must_colors = _must_colors(change)
        prefer_colors = _prefer_colors(change)
        chain: list[RelaxationLevel] = []

        # Level 0: exact (everything already satisfied).
        if cov.exact_ids:
            chain.append(
                RelaxationLevel(
                    level=0,
                    kind="exact",
                    candidate_ids=list(cov.exact_ids),
                    description="完全满足所有硬条件",
                )
            )

        # Level 1: drop PREFER colours (softest relaxable dimension).  The MUST
        # target and colour gate are untouched -- only the user's *preference*
        # is surrendered, and only when some exact candidate misses it.  This
        # is the reporting level: its candidates sit inside exact, so Agent 2
        # can relax pink while keeping hairwear strict (RELAX_PREFERENCE).
        if cov.prefer_missed_ids and prefer_colors:
            dropped = " / ".join(_color_label(color) for color in prefer_colors)
            gap_note = (
                "（衣橱无这些颜色的候选）"
                if cov.unmet_prefer_colors
                else "（接受非偏好色候选）"
            )
            chain.append(
                RelaxationLevel(
                    level=1,
                    dropped=dropped,
                    kind="color",
                    candidate_ids=list(cov.prefer_missed_ids),
                    description=f"放弃偏好色 {dropped}{gap_note}",
                )
            )

        # Level 2: drop MUST colours, keep the type target.
        if must_colors:
            color_relaxed = [uid for uid in cov.relaxed_ids if uid not in cov.exact_ids]
            if color_relaxed:
                dropped = " / ".join(_color_label(color) for color in must_colors)
                chain.append(
                    RelaxationLevel(
                        level=2,
                        dropped=dropped,
                        kind="color",
                        candidate_ids=color_relaxed,
                        description=f"放宽颜色（不再要求 {dropped}）",
                    )
                )

        # Level 3: broaden item type to the target's slot (still outside the
        # REMOVE exclusions).  Offered even when level 0 is satisfiable, so
        # Agent 2 has more than one item to build a full outfit.
        target_slot = _target_slot(change.target)
        if target_slot:
            broadened = [
                item.item_id
                for item in wardrobe
                if item.item_id not in excluded
                and infer_slot(item.item_type) == target_slot
                and not _change_matches_type(item, change.target)
            ]
            if broadened:
                chain.append(
                    RelaxationLevel(
                        level=3,
                        kind="type",
                        candidate_ids=broadened,
                        description=(
                            f"放宽品类（同槽位 {target_slot} 的其他单品）"
                        ),
                    )
                )

        exact_count = len(cov.exact_ids)
        unmet = not any(level.candidate_ids for level in chain)
        minimal = None
        for level in chain:
            if level.candidate_ids:
                minimal = level.level
                break
        options.append(
            RelaxationOption(
                change_id=cov.change_id,
                source_text=cov.source_text,
                target=cov.target,
                exact_count=exact_count,
                chain=chain,
                minimal_level=minimal,
                unmet=unmet,
                unmet_prefer_colors=list(cov.unmet_prefer_colors),
            )
        )
    return RelaxationPlan(feasibility=pool.feasibility, options=options)
