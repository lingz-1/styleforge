"""Explainable deterministic scoring for the image-independent baseline."""

from __future__ import annotations

import re
from collections.abc import Sequence

from styleforge.core.schemas import CatalogItem, TaskSpec


NEUTRAL_COLORS = {
    "black",
    "white",
    "gray",
    "grey",
    "beige",
    "cream",
    "ivory",
    "brown",
    "tan",
    "navy",
    "silver",
    "gold",
}

OCCASION_KEYWORDS = {
    "business": {"blazer", "tailored", "shirt", "loafer", "pump", "trouser", "leather"},
    "formal": {"elegant", "formal", "dress", "heel", "silk", "satin", "tailored"},
    "casual": {"casual", "denim", "sneaker", "tee", "t-shirt", "knit", "flat"},
    "sport": {"sport", "athletic", "sneaker", "running", "active"},
    "date": {"elegant", "dress", "heel", "romantic", "silk", "lace"},
    "daily": set(),
}

FORMALITY_POSITIVE = {
    "tailored": 20.0,
    "blazer": 22.0,
    "suit": 22.0,
    "button-up": 16.0,
    "button up": 16.0,
    "collared": 14.0,
    "blouse": 14.0,
    "shirt": 12.0,
    "trouser": 18.0,
    "pencil skirt": 18.0,
    "midi skirt": 10.0,
    "loafer": 16.0,
    "pump": 16.0,
    "oxford": 18.0,
    "structured": 14.0,
    "leather": 8.0,
    "wool": 8.0,
    "elegant": 8.0,
}

FORMALITY_NEGATIVE = {
    "acid wash": 38.0,
    "distressed": 40.0,
    "ripped": 40.0,
    "torn": 35.0,
    "jeans": 28.0,
    "denim": 24.0,
    "graphic": 24.0,
    "sneaker": 30.0,
    "athletic": 35.0,
    "sport": 24.0,
    "hoodie": 30.0,
    "sweatshirt": 25.0,
    "crop top": 24.0,
    "mini skirt": 20.0,
    "high-low": 16.0,
    "high low": 16.0,
}

PROFESSIONAL_COLORS = {
    "black",
    "navy",
    "gray",
    "grey",
    "white",
    "cream",
    "ivory",
    "beige",
    "brown",
    "tan",
    "camel",
    "charcoal",
    "anthracite",
}

BRIGHT_BUSINESS_COLORS = {
    "aqua",
    "neon",
    "lime",
    "hot pink",
    "orange",
    "bright yellow",
    "electric blue",
}


def normalize_color(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def color_matches(color: str, query: str) -> bool:
    normalized_color = normalize_color(color)
    normalized_query = normalize_color(query)
    return bool(normalized_query) and normalized_query in normalized_color


def _color_coherence(items: Sequence[CatalogItem]) -> float:
    colors = [normalize_color(item.color) for item in items if item.color.strip()]
    if not colors:
        return 50.0
    unique = set(colors)
    if len(unique) == 1:
        return 100.0
    non_neutral = {
        color
        for color in unique
        if not any(neutral in color for neutral in NEUTRAL_COLORS)
    }
    if len(non_neutral) <= 1:
        return 90.0
    if len(non_neutral) == 2:
        return 72.0
    return 48.0


def _preference_match(items: Sequence[CatalogItem], task: TaskSpec) -> float:
    if not task.preferred_colors:
        return 100.0
    matched = sum(
        1
        for item in items
        if any(color_matches(item.color, preferred) for preferred in task.preferred_colors)
    )
    return 100.0 * matched / len(items)


def _occasion_match(items: Sequence[CatalogItem], occasion: str) -> float:
    normalized = occasion.strip().lower()
    keywords = OCCASION_KEYWORDS.get(normalized, {normalized} if normalized else set())
    if not keywords:
        return 80.0
    text = " ".join(f"{item.name} {item.description}" for item in items).lower()
    hits = sum(1 for keyword in keywords if keyword in text)
    if hits == 0:
        return 55.0
    return min(100.0, 65.0 + 12.0 * hits)


def item_formality_score(item: CatalogItem, occasion: str) -> float:
    """Estimate item-level formality from auditable metadata evidence."""
    if occasion not in {"business", "formal"}:
        return 80.0
    base_by_type = {
        "top": 55.0,
        "pants": 62.0,
        "skirt": 60.0,
        "shoes": 58.0,
        "dress": 64.0,
        "jumpsuit": 58.0,
        "outwear": 68.0,
        "bag": 65.0,
    }
    score = base_by_type.get(item.item_type, 55.0)
    text = f"{item.name} {item.description} {' '.join(item.features)}".lower()
    for keyword, value in FORMALITY_POSITIVE.items():
        if keyword in text:
            score += value
    for keyword, value in FORMALITY_NEGATIVE.items():
        if keyword in text:
            score -= value
    color = normalize_color(item.color)
    if any(value in color for value in PROFESSIONAL_COLORS):
        score += 10.0
    if any(value in color for value in BRIGHT_BUSINESS_COLORS):
        score -= 16.0
    return max(0.0, min(100.0, score))


def _formality_match(items: Sequence[CatalogItem], occasion: str) -> float:
    if not items:
        return 0.0
    scores = [item_formality_score(item, occasion) for item in items]
    mean_score = sum(scores) / len(scores)
    # One casual item can undermine an otherwise formal interview outfit.
    return 0.6 * mean_score + 0.4 * min(scores)


def score_outfit(
    items: Sequence[CatalogItem],
    task: TaskSpec,
) -> tuple[float, dict[str, float], tuple[str, ...]]:
    details = {
        "slot_completeness": 100.0,
        "color_coherence": _color_coherence(items),
        "preference_match": _preference_match(items, task),
        "occasion_match": _occasion_match(items, task.occasion),
        "formality_match": _formality_match(items, task.occasion),
    }
    if task.occasion in {"business", "formal"}:
        score = (
            0.15 * details["slot_completeness"]
            + 0.15 * details["color_coherence"]
            + 0.10 * details["preference_match"]
            + 0.25 * details["occasion_match"]
            + 0.35 * details["formality_match"]
        )
    else:
        score = (
            0.25 * details["slot_completeness"]
            + 0.25 * details["color_coherence"]
            + 0.20 * details["preference_match"]
            + 0.15 * details["occasion_match"]
            + 0.15 * details["formality_match"]
        )
    reasons = (
        f"满足 {len(task.required_slots)} 个必需槽位",
        f"颜色协调度 {details['color_coherence']:.0f}/100",
        f"场景匹配度 {details['occasion_match']:.0f}/100",
        f"正式度 {details['formality_match']:.0f}/100",
    )
    return round(score, 2), details, reasons
