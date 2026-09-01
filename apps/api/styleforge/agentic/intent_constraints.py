"""Deterministic checks for explicit outfit requirements.

The LLM remains responsible for taste and trade-offs.  This module only turns
requirements with an objective wardrobe representation (for example
``comfortable`` footwear for long standing) into a small, auditable gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from styleforge.core.categories import infer_slot


@dataclass(frozen=True, slots=True)
class FeatureRequirement:
    feature: str
    reason: str
    slot: str | None = None


def desired_features_for_request(request: str) -> set[str]:
    """Features useful for deterministic ranking and candidate recovery."""
    text = (request or "").lower()
    desired: set[str] = set()
    rules = (
        (("婚礼", "wedding"), ("wedding", "formal", "elegant")),
        (("约会", "date"), ("date", "evening", "elegant")),
        (("通勤", "上班", "客户", "面试", "commute"), ("formal", "minimal", "practical")),
        (("久站", "舒适", "耐走", "好走", "走很多", "comfortable"), ("comfortable",)),
        (
            ("篮球", "训练", "健身", "跑步", "运动", "basketball", "training", "sport"),
            ("sport", "breathable", "cushioned", "non_slip"),
        ),
        (("下雨", "雨天", "有雨", "防水", "rain", "waterproof"), ("waterproof", "non_slip")),
        (("防滑", "non-slip", "non_slip"), ("non_slip",)),
        (("降温", "保暖", "寒冷", "冷天", "warm"), ("warm",)),
        (("简约", "极简", "minimal"), ("minimal",)),
        (("正式", "专业", "formal", "professional"), ("formal",)),
    )
    for markers, features in rules:
        if any(marker in text for marker in markers):
            desired.update(features)
    return desired


def explicit_feature_requirements(request: str) -> tuple[FeatureRequirement, ...]:
    """Return only requirements safe enough to enforce without LLM judgement."""
    text = (request or "").lower()
    requirements: list[FeatureRequirement] = []

    def add(feature: str, reason: str, slot: str | None = None) -> None:
        requirement = FeatureRequirement(feature=feature, reason=reason, slot=slot)
        if requirement not in requirements:
            requirements.append(requirement)

    if any(word in text for word in ("久站", "舒适", "耐走", "好走", "走很多", "comfortable")):
        add("comfortable", "久站或步行舒适", "footwear")
    if any(word in text for word in ("篮球", "basketball")):
        add("sport", "篮球运动")
        add("cushioned", "篮球缓震", "footwear")
    elif any(word in text for word in ("训练", "健身", "跑步", "运动", "training", "sport")):
        add("sport", "运动场景")
    if any(word in text for word in ("防水", "waterproof")):
        add("waterproof", "明确要求防水")
    if any(word in text for word in ("防滑", "non-slip", "non_slip")):
        add("non_slip", "明确要求防滑", "footwear")
    if any(word in text for word in ("降温", "保暖", "寒冷", "冷天", "warm")):
        add("warm", "明确要求保暖")
    if any(word in text for word in ("简约", "极简", "minimal")):
        add("minimal", "明确要求简约")
    negates_formal = any(
        phrase in text
        for phrase in (
            "不要正式",
            "别太正式",
            "不要太正式",
            "太正式了",
            "不那么正式",
            "更休闲",
        )
    )
    if not negates_formal and any(
        word in text for word in ("正式", "专业", "客户", "面试", "formal", "professional")
    ):
        add("formal", "明确要求正式或专业")
    return tuple(requirements)


def missing_feature_requirements(
    request: str,
    selected_items: Iterable[Any],
    *,
    available_items: Iterable[Any] | None = None,
) -> list[FeatureRequirement]:
    """Check requirements against authoritative catalog item metadata."""
    items = list(selected_items)
    available = list(available_items) if available_items is not None else None
    missing: list[FeatureRequirement] = []
    for requirement in explicit_feature_requirements(request):
        def eligible(source: list[Any]) -> list[Any]:
            if requirement.slot is None:
                return source
            return [
                item
                for item in source
                if infer_slot(str(getattr(item, "item_type", ""))) == requirement.slot
            ]

        def satisfied(source: list[Any]) -> bool:
            return any(
                requirement.feature
                in {str(feature).lower() for feature in getattr(item, "features", ())}
                for item in eligible(source)
            )

        # Older/imported wardrobes may have no feature annotations at all. A
        # deterministic gate cannot truthfully reject in that situation.
        if available is not None and not satisfied(available):
            continue
        if not satisfied(items):
            missing.append(requirement)
    return missing
