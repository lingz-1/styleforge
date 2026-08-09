"""Deterministic item facts exposed to the three extension agents as tools."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from styleforge.core.categories import infer_slot
from styleforge.core.garment_attributes import SUBTYPE_RULES, item_matches_subtype
from styleforge.core.schemas import CatalogItem, EmbeddingStatus, ImageStatus
from styleforge.models.task import CandidateItem


NEUTRAL_COLOR_TERMS = (
    "black",
    "white",
    "gray",
    "grey",
    "beige",
    "cream",
    "brown",
    "navy",
    "黑",
    "白",
    "灰",
    "米",
    "棕",
)

COLOR_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("black", ("black", "黑色", "黑")),
    ("white", ("white", "白色", "白")),
    ("gray", ("gray", "grey", "灰色", "灰")),
    ("brown", ("brown", "棕色", "棕", "咖色", "咖啡色")),
    ("beige", ("beige", "米色", "米白", "卡其")),
    ("blue", ("blue", "蓝色", "蓝", "牛仔蓝")),
    ("red", ("red", "红色", "酒红", "红")),
    ("green", ("green", "绿色", "橄榄绿", "绿")),
    ("purple", ("purple", "紫色", "紫")),
)

ITEM_CONCEPTS: tuple[dict[str, Any], ...] = (
    {
        "concept": "vest",
        "aliases": ("马甲", "针织背心", "西装背心", "vest", "waistcoat"),
        "item_type": "top",
        "allowed_item_types": ("top", "outwear"),
        "subtype": "vest",
    },
    {
        "concept": "cowboy_boots",
        "aliases": ("牛仔靴", "西部靴", "cowboy boots", "cowboy boot"),
        "item_type": "shoes",
        "subtype": "boots",
    },
    {
        "concept": "boots",
        "aliases": ("靴子", "短靴", "长靴", "boots", "boot"),
        "item_type": "shoes",
        "subtype": "boots",
    },
    {
        "concept": "trench_coat",
        "aliases": ("风衣", "trench coat"),
        "item_type": "outwear",
        "subtype": "trench_coat",
    },
    {
        "concept": "coat",
        "aliases": ("大衣", "外套", "coat"),
        "item_type": "outwear",
        "subtype": "coat",
    },
    {
        "concept": "jacket",
        "aliases": ("夹克", "jacket"),
        "item_type": "outwear",
        "subtype": "jacket",
    },
    {
        "concept": "shirt",
        "aliases": ("衬衫", "衬衣", "shirt", "blouse"),
        "item_type": "top",
        "subtype": "shirt",
    },
    {
        "concept": "knitwear",
        "aliases": ("毛衣", "针织衫", "sweater", "knitwear"),
        "item_type": "top",
        "subtype": "knitwear",
    },
    {
        "concept": "skirt",
        "aliases": ("半身裙", "裙子", "skirt"),
        "item_type": "skirt",
        "subtype": "",
    },
    {
        "concept": "pants",
        "aliases": ("裤子", "长裤", "pants", "trousers"),
        "item_type": "pants",
        "subtype": "",
    },
    {
        "concept": "dress",
        "aliases": ("连衣裙", "dress"),
        "item_type": "dress",
        "subtype": "",
    },
    {
        "concept": "shoes",
        "aliases": ("鞋子", "鞋", "shoes"),
        "item_type": "shoes",
        "subtype": "",
    },
    {
        "concept": "bag",
        "aliases": ("包包", "包袋", "包", "bag"),
        "item_type": "bag",
        "subtype": "",
    },
)


def matching_subtypes(item: CatalogItem) -> list[str]:
    matches: list[str] = []
    for subtype in SUBTYPE_RULES:
        try:
            if item_matches_subtype(item, subtype):
                matches.append(subtype)
        except ValueError:
            continue
    return matches


def item_summary(item: CatalogItem) -> dict[str, Any]:
    return {
        "item_id": item.item_id,
        "name": item.name,
        "item_type": item.item_type,
        "slot": infer_slot(item.item_type),
        "color": item.color,
        "description": item.description,
        "subtypes": matching_subtypes(item),
        "image_status": item.image_status.value,
        "image_url": f"/items/{item.item_id}/image",
    }


def candidate_to_catalog_item(candidate: CandidateItem) -> CatalogItem:
    features = tuple(value for value in (candidate.subtype,) if value)
    return CatalogItem(
        item_id=candidate.item_id,
        source="candidate-preview",
        gender=candidate.gender,
        item_type=candidate.item_type,
        main_category=candidate.item_type,
        name=candidate.name or candidate.item_type,
        color=candidate.color,
        description=candidate.description,
        features=features,
        image_filename="",
        relative_image_path="",
        image_status=ImageStatus.UNBOUND,
        embedding_status=EmbeddingStatus.PENDING,
    )


def pair_compatibility(left: CatalogItem, right: CatalogItem) -> float:
    left_color = left.color.strip().lower()
    right_color = right.color.strip().lower()
    if not left_color or not right_color:
        color_score = 65.0
    elif left_color == right_color:
        color_score = 86.0
    elif any(term in left_color for term in NEUTRAL_COLOR_TERMS) or any(
        term in right_color for term in NEUTRAL_COLOR_TERMS
    ):
        color_score = 90.0
    else:
        color_score = 70.0

    left_tokens = set(
        re.findall(r"[a-z\u4e00-\u9fff]+", f"{left.name} {left.description}".lower())
    )
    right_tokens = set(
        re.findall(r"[a-z\u4e00-\u9fff]+", f"{right.name} {right.description}".lower())
    )
    shared = len(left_tokens & right_tokens)
    metadata_score = min(100.0, 60.0 + 8.0 * shared)
    return round(0.75 * color_score + 0.25 * metadata_score, 2)


def extract_color(request: str) -> str:
    normalized = request.lower()
    for canonical, aliases in COLOR_ALIASES:
        if any(alias in normalized for alias in aliases):
            return canonical
    return ""


def extract_item_concept(request: str) -> dict[str, Any] | None:
    normalized = request.lower()
    for concept in ITEM_CONCEPTS:
        if any(alias in normalized for alias in concept["aliases"]):
            return concept
    return None


def infer_candidate_item(request: str) -> CandidateItem | None:
    concept = extract_item_concept(request)
    if concept is None:
        return None
    color = extract_color(request)
    name = " ".join(value for value in (color, concept["concept"]) if value)
    return CandidateItem(
        name=name or str(concept["concept"]),
        item_type=str(concept["item_type"]),
        subtype=str(concept["subtype"]),
        color=color,
        description=request.strip(),
    )


def resolve_anchor_items(
    wardrobe: list[CatalogItem],
    request: str,
    *,
    item_id: str = "",
) -> list[CatalogItem]:
    if item_id:
        return [item for item in wardrobe if item.item_id == item_id]
    concept = extract_item_concept(request)
    color = extract_color(request)
    normalized = request.lower()
    ranked: list[tuple[float, str, CatalogItem]] = []
    for item in wardrobe:
        text = f"{item.name} {item.description} {' '.join(item.features)}".lower()
        score = 0.0
        if concept is not None:
            allowed_types = set(
                concept.get("allowed_item_types", (concept["item_type"],))
            )
            if item.item_type not in allowed_types:
                continue
            if item.item_type == concept["item_type"]:
                score += 25.0
            if any(alias in text for alias in concept["aliases"]):
                score += 65.0
            if concept["subtype"] and concept["subtype"] in matching_subtypes(item):
                score += 45.0
        if color:
            if color not in item.color.lower() and color not in text:
                continue
            score += 35.0
        name_tokens = set(re.findall(r"[a-z]+", normalized))
        score += min(20.0, 4.0 * len(name_tokens & set(re.findall(r"[a-z]+", text))))
        if score >= 45.0:
            ranked.append((score, item.item_id, item))
    ranked.sort(key=lambda value: (-value[0], value[1]))
    if not ranked:
        return []
    best_score = ranked[0][0]
    return [item for score, _, item in ranked if score >= best_score - 8.0][:6]


def group_compatible_items(
    anchor: CatalogItem,
    wardrobe: list[CatalogItem],
    slots: set[str],
    *,
    per_slot: int = 8,
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[tuple[float, CatalogItem]]] = defaultdict(list)
    for item in wardrobe:
        if item.item_id == anchor.item_id:
            continue
        slot = infer_slot(item.item_type)
        if slot not in slots:
            continue
        grouped[slot].append((pair_compatibility(anchor, item), item))
    result: dict[str, list[dict[str, Any]]] = {}
    for slot, ranked in grouped.items():
        ranked.sort(key=lambda value: (-value[0], value[1].item_id))
        result[slot] = [
            {**item_summary(item), "pair_score": score}
            for score, item in ranked[:per_slot]
            if score >= 58.0
        ]
    return result
