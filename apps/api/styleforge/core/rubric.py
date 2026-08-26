"""Unified five-dimension evaluation rubric shared by all three agents.

The v1.1 evaluation spec makes the five dimensions the single evaluation
language of the decision graph: Agent 1 prepares information for them, Agent 2
uses them as composition targets, Agent 3 is the only formal scoring node.
User-configured weights only change the importance of the existing dimensions,
never add new ones.
"""

from __future__ import annotations

from typing import Any

# (key, zh_name, en_name, core_question)
FIVE_DIMENSIONS: tuple[tuple[str, str, str, str], ...] = (
    (
        "request_relevance",
        "需求还原度",
        "Request Relevance",
        "推荐是否真正满足用户完整需求？",
    ),
    (
        "request_specificity",
        "请求特异性",
        "Request Specificity",
        "推荐是否真正针对当前请求，而非泛化日常搭配？",
    ),
    (
        "outfit_coordination",
        "搭配协调性",
        "Outfit Coordination",
        "各单品组合后是否形成协调完整的穿搭？",
    ),
    (
        "wearability",
        "实穿性",
        "Wearability",
        "方案在真实场景中是否合理可穿？",
    ),
    (
        "freshness",
        "新鲜感",
        "Freshness",
        "是否避免近期推荐的机械重复？",
    ),
)

DEFAULT_EVALUATION_WEIGHTS: dict[str, float] = {
    "request_relevance": 0.25,
    "request_specificity": 0.25,
    "outfit_coordination": 0.20,
    "wearability": 0.15,
    "freshness": 0.15,
}

MIN_WEIGHT = 0.05


def dimension_keys() -> tuple[str, ...]:
    return tuple(key for key, _, _, _ in FIVE_DIMENSIONS)


def normalize_weights(weights: dict[str, Any] | None) -> dict[str, float]:
    """Merge user weights over the defaults, then renormalize to sum 1.

    Every dimension keeps at least ``MIN_WEIGHT`` so no facet can be zeroed
    out entirely and silently degrade decisions. The floor is enforced
    iteratively so the final result still sums to exactly 1.
    """
    merged: dict[str, float] = dict(DEFAULT_EVALUATION_WEIGHTS)
    if weights:
        for key in dimension_keys():
            value = weights.get(key)
            if isinstance(value, (int, float)) and value >= 0:
                merged[key] = float(value)
    total = sum(merged.values())
    if total <= 0:
        return dict(DEFAULT_EVALUATION_WEIGHTS)
    result = {key: value / total for key, value in merged.items()}
    keys = dimension_keys()
    while True:
        below = [key for key in keys if result[key] < MIN_WEIGHT]
        if not below:
            break
        for key in below:
            result[key] = MIN_WEIGHT
        above = [key for key in keys if result[key] > MIN_WEIGHT]
        if not above:
            break
        remaining = 1.0 - MIN_WEIGHT * len(below)
        above_total = sum(result[key] for key in above)
        if above_total <= 0:
            break
        for key in above:
            result[key] = result[key] * remaining / above_total
    return result


def rubric_text(weights: dict[str, float] | None = None) -> str:
    """Render the shared five-dimension rubric (with weights) as prompt text."""
    resolved = normalize_weights(weights)
    lines = ["统一五维评价标准（用户权重影响各维度重要程度）："]
    for key, zh_name, en_name, question in FIVE_DIMENSIONS:
        weight = resolved[key]
        lines.append(
            f"- {zh_name}（{en_name}，权重 {weight * 100:.0f}%）：{question}"
        )
    return "\n".join(lines)


def aggregate_score(
    dimension_scores: dict[str, Any] | None,
    weights: dict[str, Any] | None = None,
) -> float:
    """Weighted five-dimension score on a 0-100 scale (0 when unscored).

    Shares one aggregation formula with the runtime critic so offline evaluation
    matches what the agent produces; unscored/missing dimensions fall back to the
    neutral 5.
    """
    if not dimension_scores:
        return 0.0
    resolved = normalize_weights(weights)
    weighted = sum(
        resolved[key] * float(dimension_scores.get(key, 5))
        for key in dimension_keys()
    )
    return round(weighted * 10, 2)
