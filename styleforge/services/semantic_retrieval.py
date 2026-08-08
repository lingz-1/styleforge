"""Multi-query weighted semantic retrieval over the user's wardrobe.

Given the three retrieval plans from the semantic retriever (core /
distinctive / supporting), builds one FashionCLIP prompt per (plan, slot),
encodes them in a single batch, scores the wardrobe allow-list for every
plan, then combines:

    final = 0.80 * norm(weighted cosine) + 0.10 * preference + 0.10 * novelty

Weights are applied to raw cosine scores *before* min-max normalization so
the per-plan distributions are not independently stretched out of scale.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from styleforge.core.categories import infer_slot
from styleforge.core.schemas import CatalogItem, TaskSpec
from styleforge.core.scoring import color_matches
from styleforge.core.slots import base_slot
from styleforge.core.structure_signature import color_family, style_tags

# Mirror of PlannerAgent's slot labels, kept here so retrieval does not depend
# on the deterministic planner for query building.
SLOT_LABELS: dict[str, str] = {
    "top": "top or blouse",
    "bottom": "pants or skirt",
    "footwear": "shoes",
    "one_piece": "dress, jumpsuit, or suit",
    "outerwear": "coat or jacket",
    "bag": "handbag or bag",
    "accessory": "fashion accessory",
    "swimwear": "swimsuit or bikini set",
    "swim_bottom": "bikini or swimwear bottom",
    "swim_coverup": "beachwear cover-up",
    "skiwear": "skiwear outfit",
    "base_layer_top": "ski base-layer top",
    "base_layer_bottom": "ski base-layer bottom",
    "activewear_bra": "sports bra",
    "sleepwear": "sleepwear or pajamas",
    "underwear": "underwear",
    "bathwear": "bathrobe or bathwear",
}

CORE_WEIGHT = 0.40
DISTINCTIVE_WEIGHT = 0.30
SUPPORTING_WEIGHT = 0.10
PREFERENCE_WEIGHT = 0.10
NOVELTY_WEIGHT = 0.10


@dataclass(frozen=True, slots=True)
class RetrievalOutcome:
    final_scores: dict[str, float]
    per_plan_raw: dict[str, dict[str, float]]
    per_plan_norm: dict[str, dict[str, float]]
    preference_scores: dict[str, float]
    novelty_scores: dict[str, float]
    prompts: list[str]
    diagnostics: dict[str, Any]


def build_query_prompts(plans: Sequence[dict[str, Any]], slots: Sequence[str]) -> list[str]:
    """One English FashionCLIP prompt per (plan, slot)."""
    prompts: list[str] = []
    for plan in plans:
        query = plan.get("query", "")
        for slot in slots:
            label = SLOT_LABELS.get(base_slot(slot), base_slot(slot))
            prompts.append(f"{label} {query}".strip())
    return prompts


def preference_scores(items: Sequence[CatalogItem], task: TaskSpec) -> dict[str, float]:
    if not task.preferred_colors:
        return {item.item_id: 0.5 for item in items}
    return {
        item.item_id: 1.0
        if any(color_matches(item.color, color) for color in task.preferred_colors)
        else 0.0
        for item in items
    }


def novelty_scores(
    items: Sequence[CatalogItem],
    recent_signatures: Sequence[dict[str, Any]],
) -> dict[str, float]:
    """Soft penalty for facets already shown in recent structure signatures."""
    if not recent_signatures:
        return {item.item_id: 0.5 for item in items}
    scores: dict[str, float] = {}
    for item in items:
        sig = (
            base_slot(infer_slot(item.item_type)),
            color_family(item.color),
            (style_tags(item) or ("basic",))[0],
        )
        exposure = 0.0
        for recent in recent_signatures:
            facets = [
                int(sig[0] in set(recent.get("category_structure", []))),
                int(sig[1] in set(recent.get("dominant_color_family", []))),
                int(sig[2] in set(recent.get("style_mix", []))),
            ]
            overlap = sum(facets) / 3.0
            exposure = max(exposure, overlap)
        scores[item.item_id] = 1.0 - exposure
    return scores


def _score_wardrobe_slot(
    query_vector: Any,
    allowed_ids: Sequence[str],
    catalog_store: Any | None,
    personal_store: Any | None,
) -> dict[str, float]:
    merged: dict[str, float] = {}
    if catalog_store is not None:
        merged.update(catalog_store.score_items(query_vector, allowed_ids))
    if personal_store is not None:
        for item_id, score in personal_store.score_items(query_vector, allowed_ids).items():
            merged[item_id] = max(merged.get(item_id, score), score)
    return merged


def score_multi_query(
    *,
    plans: Sequence[dict[str, Any]],
    wardrobe_items: Sequence[CatalogItem],
    by_slot: dict[str, list[str]],
    encoder: Any,
    catalog_store: Any | None,
    personal_store: Any | None,
    preference: dict[str, float],
    novelty: dict[str, float],
) -> RetrievalOutcome:
    slots = [slot for slot in by_slot if by_slot[slot]]
    prompts = build_query_prompts(plans, slots)
    query_vectors = encoder.encode_texts(prompts)

    plan_raw: dict[str, dict[str, float]] = defaultdict(dict)
    index = 0
    for plan in plans:
        for slot in slots:
            query_vector = query_vectors[index]
            index += 1
            raw = _score_wardrobe_slot(query_vector, by_slot[slot], catalog_store, personal_store)
            for item_id, score in raw.items():
                previous = plan_raw[plan["type"]].get(item_id)
                if previous is None or score > previous:
                    plan_raw[plan["type"]][item_id] = score

    weighted: dict[str, float] = {}
    for plan in plans:
        weight = float(plan.get("score_weight", 0.0))
        for item_id, score in plan_raw[plan["type"]].items():
            weighted[item_id] = weighted.get(item_id, 0.0) + weight * score

    per_plan_norm: dict[str, dict[str, float]] = {}
    norm: dict[str, float] = {}
    if weighted:
        low = min(weighted.values())
        high = max(weighted.values())
        span = high - low
        for item_id, score in weighted.items():
            norm[item_id] = 0.5 if span <= 1e-12 else (score - low) / span
        for plan in plans:
            raw_scores = plan_raw[plan["type"]]
            if raw_scores:
                plan_low = min(raw_scores.values())
                plan_high = max(raw_scores.values())
                plan_span = plan_high - plan_low
                per_plan_norm[plan["type"]] = {
                    item_id: (
                        0.5
                        if plan_span <= 1e-12
                        else (score - plan_low) / plan_span
                    )
                    for item_id, score in raw_scores.items()
                }
            else:
                per_plan_norm[plan["type"]] = {}

    final_scores: dict[str, float] = {}
    for item_id, norm_score in norm.items():
        final_scores[item_id] = (
            (CORE_WEIGHT + DISTINCTIVE_WEIGHT + SUPPORTING_WEIGHT) * norm_score
            + PREFERENCE_WEIGHT * preference.get(item_id, 0.5)
            + NOVELTY_WEIGHT * novelty.get(item_id, 0.5)
        )

    diagnostics: dict[str, Any] = {
        "prompt_count": len(prompts),
        "scored_item_count": len(final_scores),
        "per_plan_scored": {
            plan_type: len(raw) for plan_type, raw in plan_raw.items()
        },
        "weights": {
            "core": CORE_WEIGHT,
            "distinctive": DISTINCTIVE_WEIGHT,
            "supporting": SUPPORTING_WEIGHT,
            "preference": PREFERENCE_WEIGHT,
            "novelty": NOVELTY_WEIGHT,
        },
    }
    return RetrievalOutcome(
        final_scores=final_scores,
        per_plan_raw={plan_type: dict(raw) for plan_type, raw in plan_raw.items()},
        per_plan_norm=per_plan_norm,
        preference_scores=dict(preference),
        novelty_scores=dict(novelty),
        prompts=prompts,
        diagnostics=diagnostics,
    )
