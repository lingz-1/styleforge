"""Top-N candidate pool construction with per-category quotas.

Builds the Top-50 pool the composer may pick from. Categories follow the
frozen quota keys (tops/bottoms/dresses/outerwear/shoes/accessories). When a
category has fewer available items than its quota, the deficit is filled from
higher-scoring items of other categories and logged for Phase 7 evaluation.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from styleforge.core.categories import infer_slot
from styleforge.core.schemas import CatalogItem, TaskSpec
from styleforge.core.scoring import color_matches, item_formality_score
from styleforge.core.structure_signature import style_tags

CATEGORY_TO_SLOT: dict[str, str] = {
    "tops": "top",
    "bottoms": "bottom",
    "dresses": "one_piece",
    "outerwear": "outerwear",
    "shoes": "footwear",
    "accessories": "accessory",
}
SLOT_TO_CATEGORY: dict[str, str] = {slot: cat for cat, slot in CATEGORY_TO_SLOT.items()}
CATEGORY_ORDER: tuple[str, ...] = tuple(CATEGORY_TO_SLOT.keys())

MAX_POOL_SIZE = 50


@dataclass(frozen=True, slots=True)
class PoolOutcome:
    pool_items: list[CatalogItem]
    pool_scores: dict[str, float]
    quota_used: dict[str, int]
    quota_transfer_log: list[dict[str, Any]]
    diagnostics: dict[str, Any]


def _slot_category(item: CatalogItem) -> str:
    return SLOT_TO_CATEGORY.get(infer_slot(item.item_type), "other")


def _rule_score(item: CatalogItem, task: TaskSpec) -> float:
    preferred = float(
        any(color_matches(item.color, color) for color in task.preferred_colors)
    )
    formality = item_formality_score(item, task.occasion)
    return 0.6 * formality + 0.4 * 100.0 * preferred


def _effective_scores(
    wardrobe_items: Sequence[CatalogItem],
    final_scores: dict[str, float],
    task: TaskSpec,
    diagnostics: dict[str, Any],
) -> dict[str, float]:
    if final_scores:
        return dict(final_scores)
    diagnostics["retrieval_degraded_to_rule"] = True
    return {item.item_id: _rule_score(item, task) for item in wardrobe_items}


def build_candidate_pool(
    *,
    wardrobe_items: Sequence[CatalogItem],
    final_scores: dict[str, float],
    requirements: dict[str, int],
    task: TaskSpec,
    max_pool_size: int = MAX_POOL_SIZE,
) -> PoolOutcome:
    diagnostics: dict[str, Any] = {}
    scores = _effective_scores(wardrobe_items, final_scores, task, diagnostics)

    grouped: dict[str, list[CatalogItem]] = defaultdict(list)
    for item in wardrobe_items:
        grouped[_slot_category(item)].append(item)
    ranked: dict[str, list[CatalogItem]] = {
        category: sorted(items, key=lambda item: -scores.get(item.item_id, 0.0))
        for category, items in grouped.items()
    }

    quota: dict[str, int] = {
        category: max(int(requirements.get(category, 0)), 0) for category in CATEGORY_ORDER
    }

    selected: dict[str, list[CatalogItem]] = {category: [] for category in CATEGORY_ORDER}
    for category in CATEGORY_ORDER:
        available = ranked.get(category, [])
        take = min(quota[category], len(available))
        selected[category] = available[:take]

    # Dynamic quota transfer: categories that could not reach their quota get
    # filled from the highest-scoring remaining items of other categories.
    deficits = [
        (category, quota[category] - len(selected[category]))
        for category in CATEGORY_ORDER
        if quota[category] - len(selected[category]) > 0
    ]
    transfer_log: list[dict[str, Any]] = []
    if deficits:
        remaining_pool: list[tuple[CatalogItem, float]] = []
        for category in CATEGORY_ORDER:
            for item in ranked.get(category, [])[len(selected[category]):]:
                remaining_pool.append((item, scores.get(item.item_id, 0.0)))
        remaining_pool.sort(key=lambda pair: -pair[1])
        for category, deficit in deficits:
            transferred = 0
            sources: dict[str, int] = defaultdict(int)
            while transferred < deficit and remaining_pool:
                item, _ = remaining_pool.pop(0)
                sources[_slot_category(item)] += 1
                selected[category].append(item)
                transferred += 1
            if transferred:
                transfer_log.append(
                    {
                        "from_category": max(sources, key=sources.get),
                        "to_category": category,
                        "deficit": deficit,
                        "transferred": transferred,
                    }
                )

    # Merge, dedupe, and truncate to the pool cap by score.
    seen: set[str] = set()
    merged: list[tuple[CatalogItem, float]] = []
    for category in CATEGORY_ORDER:
        for item in selected[category]:
            if item.item_id in seen:
                continue
            seen.add(item.item_id)
            merged.append((item, scores.get(item.item_id, 0.0)))
    merged.sort(key=lambda pair: (-pair[1], pair[0].item_id))
    pool_items = [item for item, _ in merged[:max_pool_size]]

    quota_used: dict[str, int] = {category: 0 for category in CATEGORY_ORDER}
    for item in pool_items:
        quota_used[_slot_category(item)] += 1

    pool_scores = {
        item.item_id: scores.get(item.item_id, 0.0) for item in pool_items
    }
    diagnostics["candidate_requirements"] = dict(requirements)
    diagnostics["quota_used"] = dict(quota_used)
    diagnostics["quota_transfer_count"] = len(transfer_log)
    diagnostics["pool_size"] = len(pool_items)
    diagnostics["pool_truncated"] = len(merged) > max_pool_size
    return PoolOutcome(
        pool_items=pool_items,
        pool_scores=pool_scores,
        quota_used=quota_used,
        quota_transfer_log=transfer_log,
        diagnostics=diagnostics,
    )


def pool_manifest(pool: PoolOutcome) -> list[dict[str, Any]]:
    """Compact item list for the composer LLM (bounded token footprint)."""
    manifest: list[dict[str, Any]] = []
    for item in pool.pool_items:
        manifest.append(
            {
                "item_id": item.item_id,
                "category": _slot_category(item),
                "color": item.color,
                "style_tags": list(style_tags(item)),
                "name": item.name[:40],
                "description": item.description[:80],
                "features": list(item.features)[:3],
                "score": round(pool.pool_scores.get(item.item_id, 0.0), 3),
            }
        )
    return manifest
