"""Deterministic checks for explicit outfit requirements.

The LLM remains responsible for taste and trade-offs.  This module only turns
requirements with an objective wardrobe representation (for example
``comfortable`` footwear for long standing) into a small, auditable gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from styleforge.core.categories import infer_slot


@dataclass(frozen=True, slots=True)
class FeatureRequirement:
    feature: str
    reason: str
    slot: str | None = None
    prohibited: bool = False


_FEATURE_TEXT_MARKERS: dict[str, tuple[str, ...]] = {
    "comfortable": (
        "comfortable", "comfort", "cushion", "padded insole", "lightweight",
        "flat", "loafer", "moccasin", "sneaker", "trainer", "running shoe",
        "舒适", "缓震", "软底", "平底", "乐福鞋", "运动鞋", "跑鞋",
    ),
    "cushioned": (
        "cushion", "padded insole", "air max", "running shoe", "trainer",
        "缓震", "气垫", "软底", "跑鞋", "训练鞋",
    ),
    "sport": (
        "sport", "athletic", "activewear", "training", "running", "basketball",
        "sneaker", "trainer", "legging", "运动", "训练", "跑步", "篮球", "球鞋",
    ),
    "formal": (
        "formal", "tailored", "trouser", "office", "business", "blazer",
        "shirt", "blouse", "oxford", "loafer", "silk", "slip dress", "gown",
        "presentation", "正装", "西装", "西裤",
        "衬衫", "乐福鞋", "礼服",
    ),
    "casual": (
        "casual", "weekend", "off-duty", "boyfriend jean", "sneaker",
        "trainer", "休闲", "周末", "牛仔裤", "运动鞋",
    ),
    "practical": (
        "practical", "easy fit", "lightweight", "pocket", "support",
        "实用", "轻量", "口袋", "支撑",
    ),
    "warm": (
        "warm", "wool", "cashmere", "fleece", "insulated", "down coat",
        "puffer", "thermal", "保暖", "羊毛", "羊绒", "抓绒", "羽绒",
    ),
    "waterproof": ("waterproof", "water-resistant", "防水", "拒水"),
    "non_slip": ("non-slip", "non slip", "anti-slip", "防滑"),
    "minimal": ("minimal", "clean-lined", "简约", "极简"),
    "high_heels": (
        "high heel", "stiletto", "platform heel", "4 inch heel", "5 inch heel",
        "高跟鞋", "细高跟", "厚底高跟",
    ),
}


def item_feature_evidence(item: Any) -> set[str]:
    """Return explicit plus conservatively inferred catalog features.

    Public/imported wardrobe rows often have an empty ``features`` array while
    their name and description still state facts such as ``stiletto``,
    ``padded insole`` or ``sports leggings``.  Those literal facts are safe to
    use in deterministic gates; no visual or brand-based property is guessed.
    """
    evidence = {str(feature).lower() for feature in getattr(item, "features", ())}
    text = " ".join(
        str(value or "")
        for value in (
            getattr(item, "item_type", ""),
            getattr(item, "name", ""),
            getattr(item, "description", ""),
        )
    ).lower()
    for feature, markers in _FEATURE_TEXT_MARKERS.items():
        if any(marker in text for marker in markers):
            evidence.add(feature)
    if "heel" in text and (
        re.search(r"\b(?:[7-9]\d|1\d\d)\s*mm\b", text)
        or re.search(r"\b[3-9](?:\.\d+)?\s*(?:inches?|inch|in\b|'')", text)
    ):
        evidence.add("high_heels")
    return evidence


def desired_features_for_request(request: str) -> set[str]:
    """Features useful for deterministic ranking and candidate recovery."""
    text = (request or "").lower()
    desired: set[str] = set()
    rules = (
        (("婚礼", "wedding"), ("wedding", "formal", "elegant")),
        (("约会", "date"), ("date", "evening", "elegant")),
        (
            (
                "通勤", "上班", "客户", "面试", "办公室", "汇报", "利落",
                "commute", "office", "presentation",
            ),
            ("formal", "minimal", "practical"),
        ),
        (("久站", "舒适", "耐走", "好走", "走很多", "comfortable"), ("comfortable",)),
        (
            ("篮球", "训练", "健身", "跑步", "运动", "basketball", "training", "sport"),
            ("sport", "breathable", "cushioned", "non_slip"),
        ),
        (("下雨", "雨天", "有雨", "防水", "rain", "waterproof"), ("waterproof", "non_slip")),
        (("防滑", "non-slip", "non_slip"), ("non_slip",)),
        (("降温", "保暖", "寒冷", "冷天", "warm"), ("warm",)),
        (("简约", "极简", "minimal"), ("minimal",)),
        (
            ("休闲", "轻松", "周末", "casual", "weekend"),
            ("comfortable", "casual", "practical"),
        ),
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
    if any(word in text for word in ("瑜伽", "热身", "yoga")):
        add("sport", "瑜伽或热身上装需适合活动", "top")
        add("sport", "瑜伽或热身下装需适合活动", "bottom")
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
        word in text
        for word in (
            "正式", "专业", "客户", "面试", "办公室", "汇报", "利落",
            "formal", "professional", "office", "presentation",
        )
    ):
        add("formal", "明确要求正式或专业")
    if any(
        phrase in text
        for phrase in (
            "不要高跟", "别穿高跟", "避免高跟", "无高跟", "no high heel",
            "without high heel", "户外烧烤", "篮球训练", "跑步训练", "户外露营",
        )
    ):
        requirement = FeatureRequirement(
            feature="high_heels",
            reason="场景或请求明确排除高跟鞋",
            slot="footwear",
            prohibited=True,
        )
        if requirement not in requirements:
            requirements.append(requirement)
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
        if not requirement.prohibited and requirement.feature not in {
            "comfortable",
            "sport",
            "cushioned",
            "waterproof",
            "non_slip",
            "warm",
        }:
            # Formality and minimalism are taste judgements. They remain useful
            # ranking signals but must not become deterministic environment
            # failures; the independent Critic owns those trade-offs.
            continue

        def eligible(source: list[Any]) -> list[Any]:
            if requirement.slot is None:
                return source
            return [
                item
                for item in source
                if infer_slot(str(getattr(item, "item_type", ""))) == requirement.slot
            ]

        def satisfied(source: list[Any]) -> bool:
            return any(requirement.feature in item_feature_evidence(item) for item in eligible(source))

        if requirement.prohibited:
            if satisfied(items):
                missing.append(requirement)
            continue

        # Older/imported wardrobes may have no feature annotations at all. A
        # deterministic gate cannot truthfully reject in that situation.
        if available is not None and not satisfied(available):
            # Objective safety/performance requirements should make the request
            # infeasible when the wardrobe contains literal metadata but no
            # matching item. Subjective style labels still defer to the Critic.
            if requirement.feature not in {"sport", "cushioned", "waterproof", "non_slip"}:
                continue
        if not satisfied(items):
            missing.append(requirement)
    return missing


def unavailable_feature_requirements(
    request: str,
    available_items: Iterable[Any],
) -> list[FeatureRequirement]:
    """Return objective hard requirements the wardrobe cannot satisfy at all."""
    available = list(available_items)
    objective = {"sport", "cushioned", "waterproof", "non_slip"}
    missing: list[FeatureRequirement] = []
    for requirement in explicit_feature_requirements(request):
        if requirement.prohibited or requirement.feature not in objective:
            continue
        eligible = available
        if requirement.slot is not None:
            eligible = [
                item
                for item in available
                if infer_slot(str(getattr(item, "item_type", ""))) == requirement.slot
            ]
        if not any(requirement.feature in item_feature_evidence(item) for item in eligible):
            missing.append(requirement)
    return missing


__all__ = [
    "FeatureRequirement",
    "desired_features_for_request",
    "explicit_feature_requirements",
    "item_feature_evidence",
    "missing_feature_requirements",
    "unavailable_feature_requirements",
]
