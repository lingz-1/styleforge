"""Bounded candidate generation with hard-constraint filtering."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Sequence

from styleforge.core.categories import infer_slot
from styleforge.core.garment_attributes import item_matches_subtype
from styleforge.core.schemas import CatalogItem, OutfitCandidate, TaskSpec
from styleforge.core.scoring import color_matches, item_formality_score, score_outfit
from styleforge.core.slots import base_slot


def _allowed(item: CatalogItem, task: TaskSpec) -> bool:
    if item.item_id in task.excluded_item_ids:
        return False
    if any(keyword in item.name for keyword in task.excluded_name_keywords):
        return False
    if task.target_audiences and item.gender not in task.target_audiences:
        return False
    slot = infer_slot(item.item_type)
    required_item_types = task.required_item_types_by_slot.get(slot, ())
    if required_item_types and item.item_type not in required_item_types:
        return False
    required_subtypes = task.required_subtypes_by_slot.get(slot, ())
    if required_subtypes and not any(
        item_matches_subtype(item, subtype) for subtype in required_subtypes
    ):
        return False
    excluded_subtypes = task.excluded_subtypes_by_slot.get(slot, ())
    if any(item_matches_subtype(item, subtype) for subtype in excluded_subtypes):
        return False
    return not any(color_matches(item.color, color) for color in task.excluded_colors)


def _item_priority(
    item: CatalogItem,
    task: TaskSpec,
    item_relevance: dict[str, float] | None,
) -> tuple[int, float, str]:
    preferred = int(
        any(color_matches(item.color, color) for color in task.preferred_colors)
    )
    relevance = 0.0 if item_relevance is None else item_relevance.get(item.item_id, 0.0)
    formality = item_formality_score(item, task.occasion)
    if task.occasion in {"business", "formal"}:
        context_priority = 0.55 * relevance + 0.45 * formality
    else:
        context_priority = relevance
    return (-preferred, -context_priority, item.item_id)


def _item_beam_score(
    item: CatalogItem,
    task: TaskSpec,
    item_relevance: dict[str, float] | None,
) -> float:
    preferred = float(
        any(color_matches(item.color, color) for color in task.preferred_colors)
    )
    relevance = 0.0 if item_relevance is None else item_relevance.get(item.item_id, 0.0)
    formality = item_formality_score(item, task.occasion)
    context = (
        0.55 * relevance + 0.45 * formality
        if task.occasion in {"business", "formal"}
        else relevance
    )
    return 120.0 * preferred + context


def generate_candidates(
    wardrobe_items: Sequence[CatalogItem],
    task: TaskSpec,
    per_slot_limit: int = 12,
    max_candidates: int = 2000,
    item_relevance: dict[str, float] | None = None,
) -> tuple[list[OutfitCandidate], dict[str, object]]:
    if per_slot_limit < 1 or max_candidates < 1:
        raise ValueError("Generation limits must be positive")

    by_slot: dict[str, list[CatalogItem]] = defaultdict(list)
    blocked_count = 0
    for item in wardrobe_items:
        if _allowed(item, task):
            by_slot[infer_slot(item.item_type)].append(item)
        else:
            blocked_count += 1

    required_slots = tuple(dict.fromkeys(task.required_slots))
    missing_slots = [slot for slot in required_slots if not by_slot.get(base_slot(slot))]
    diagnostics: dict[str, object] = {
        "wardrobe_item_count": len(wardrobe_items),
        "blocked_item_count": blocked_count,
        "target_audiences": list(task.target_audiences),
        "required_slots": list(required_slots),
        "required_item_types_by_slot": {
            slot: list(item_types)
            for slot, item_types in task.required_item_types_by_slot.items()
        },
        "required_subtypes_by_slot": {
            slot: list(subtypes)
            for slot, subtypes in task.required_subtypes_by_slot.items()
        },
        "excluded_subtypes_by_slot": {
            slot: list(subtypes)
            for slot, subtypes in task.excluded_subtypes_by_slot.items()
        },
        "missing_slots": missing_slots,
        "available_by_slot": {slot: len(items) for slot, items in sorted(by_slot.items())},
        "generation_truncated": False,
    }
    if missing_slots:
        diagnostics["blocking_reason"] = "required_slot_empty_after_hard_constraints"
        return [], diagnostics

    pools = [
        sorted(
            by_slot[base_slot(slot)],
            key=lambda item: _item_priority(item, task, item_relevance),
        )[:per_slot_limit]
        for slot in required_slots
    ]

    beam: list[tuple[tuple[CatalogItem, ...], dict[str, str], float]] = [
        ((), {}, 0.0)
    ]
    for slot, pool in zip(required_slots, pools, strict=True):
        expanded: list[tuple[tuple[CatalogItem, ...], dict[str, str], float]] = []
        for items, slot_items, partial_score in beam:
            used_ids = {item.item_id for item in items}
            for item in pool:
                if item.item_id in used_ids:
                    continue
                expanded.append(
                    (
                        (*items, item),
                        {**slot_items, slot: item.item_id},
                        partial_score + _item_beam_score(item, task, item_relevance),
                    )
                )
        expanded.sort(
            key=lambda value: (
                -value[2],
                tuple(item.item_id for item in value[0]),
            )
        )
        if len(expanded) > max_candidates:
            diagnostics["generation_truncated"] = True
        beam = expanded[:max_candidates]
        if not beam:
            break

    candidates: list[OutfitCandidate] = []
    for combination, slot_items, _ in beam:
        item_ids = tuple(item.item_id for item in combination)
        if len(set(item_ids)) != len(item_ids):
            continue
        signature = "|".join(item_ids)
        outfit_id = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
        score, score_details, reasons = score_outfit(combination, task)
        if item_relevance:
            semantic_relevance = sum(
                item_relevance.get(item.item_id, 0.0) for item in combination
            ) / len(combination)
            score_details["semantic_relevance"] = semantic_relevance
            score = 0.75 * score + 0.25 * semantic_relevance
            reasons = (*reasons, f"语义匹配度 {semantic_relevance:.0f}/100")
        candidates.append(
            OutfitCandidate(
                outfit_id=outfit_id,
                item_ids=item_ids,
                slot_items=slot_items,
                hard_valid=True,
                score=round(score, 2),
                score_details=score_details,
                reasons=reasons,
            )
        )

    diagnostics["generated_candidate_count"] = len(candidates)
    return candidates, diagnostics


def select_diverse_candidates(
    candidates: Sequence[OutfitCandidate],
    limit: int,
    max_jaccard_similarity: float = 0.49,
) -> list[OutfitCandidate]:
    ranked = sorted(candidates, key=lambda candidate: (-candidate.score, candidate.outfit_id))
    selected: list[OutfitCandidate] = []
    deferred: list[OutfitCandidate] = []

    for candidate in ranked:
        item_set = set(candidate.item_ids)
        sufficiently_different = True
        for existing in selected:
            existing_set = set(existing.item_ids)
            union = item_set | existing_set
            similarity = len(item_set & existing_set) / max(len(union), 1)
            if similarity > max_jaccard_similarity:
                sufficiently_different = False
                break
        if sufficiently_different:
            selected.append(candidate)
        else:
            deferred.append(candidate)
        if len(selected) >= limit:
            return selected

    selected.extend(deferred[: max(0, limit - len(selected))])
    return selected
