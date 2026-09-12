"""Official Polyvore Outfits (p-outfit) data loading and normalization.

Pure functions over the official evaluation files under ``E:\\01-style-dataset\\
p-outfit``. The eval runner reads self-contained case files
(``evals/cases/p_outfit.json``) that embed every item's text, so this module is
only needed at case-build time and for unit tests. Everything here is
deterministic and offline.

Known p-outfit facts encoded in this module:
- ``nondisjoint/train.json`` holds ``{set_id, items:[{item_id, index}]}``; only
  its item ids have 100% metadata coverage, so eval cases are sampled from it.
- ``polyvore_item_metadata.json`` maps ``item_id -> {url_name, description,
  title, semantic_category, category_id}``. ``url_name`` is always non-empty
  (the text fallback), while ``title``/``description`` are ~70% empty.
- ``semantic_category`` has 11 coarse classes (tops / bottoms / all-body /
  outerwear / shoes / bags / jewellery / sunglasses / accessories / hats /
  scarves); the official data has no color field.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from styleforge.core.categories import ALLOWED_ITEM_TYPES, infer_slot
from styleforge.core.schemas import CatalogItem, EmbeddingStatus, ImageStatus

# --- semantic_category -> project item_type ---------------------------------
# Coarse mapping only promises ``infer_slot != "other"`` and membership in
# ALLOWED_ITEM_TYPES. Finer fidelity is left to :data:`REFINE_RULES`.
SEMANTIC_TO_ITEM_TYPE: dict[str, str] = {
    "tops": "top",
    "bottoms": "pants",
    "all-body": "dress",
    "outerwear": "outwear",
    "shoes": "shoes",
    "bags": "bag",
    "jewellery": "jewellery",
    "sunglasses": "eyewear",
    "accessories": "accessory",
    "hats": "hats",
    "scarves": "accessory",
}

# (semantic_category, keyword) -> refined item_type. The keyword is matched
# case-insensitively against "title description url_name"; the refined type
# must stay in ALLOWED_ITEM_TYPES and keep the coarse type's slot.
_REFINE_RULES: dict[str, tuple[tuple[str, str], ...]] = {
    "bottoms": (
        ("skirt", "skirt"),
        ("short", "shorts"),
        ("trouser", "pants"),
        ("jean", "pants"),
        ("pant", "pants"),
    ),
    "all-body": (
        ("jumpsuit", "jumpsuit"),
        ("romper", "jumpsuit"),
        ("tuxedo", "suit"),
        ("suit", "suit"),
        ("gown", "dress"),
        ("dress", "dress"),
    ),
}

# Chinese display labels used in judge text blocks and drafted requests.
ITEM_TYPE_ZH: dict[str, str] = {
    "top": "上衣",
    "pants": "长裤",
    "shorts": "短裤",
    "skirt": "半身裙",
    "dress": "连衣裙",
    "jumpsuit": "连体裤",
    "suit": "套装",
    "outfit_set": "成套服装",
    "outwear": "外套",
    "shoes": "鞋履",
    "bag": "包袋",
    "belts": "腰带",
    "jewellery": "珠宝",
    "eyewear": "墨镜",
    "earrings": "耳饰",
    "necklace": "项链",
    "bracelet": "手链",
    "rings": "戒指",
    "watches": "腕表",
    "hats": "帽子",
    "hairwear": "发饰",
    "neckwear": "围巾",
    "gloves": "手套",
    "legwear": "腿部单品",
    "accessory": "配饰",
    "other": "单品",
}

# English keyword -> Chinese descriptor, used to reverse-draft requests from an
# outfit title/description. Ordered; first keyword hit wins per descriptor set.
_STYLE_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("silk",), "丝质"),
    (("floral", "flower", "bloom"), "花卉"),
    (("denim",), "牛仔"),
    (("embroider",), "刺绣"),
    (("lace",), "蕾丝"),
    (("velvet",), "天鹅绒"),
    (("leather",), "皮革"),
    (("chiffon",), "雪纺"),
    (("sequin", "glitter", "sparkl"), "亮片"),
    (("knit", "cardigan", "sweater"), "针织"),
    (("oversized", "baggy", "relaxed"), "宽松"),
    (("high-waist", "high waist"), "高腰"),
    (("crop",), "短款"),
    (("mini",), "迷你"),
    (("midi",), "中长"),
    (("maxi",), "长款"),
    (("vintage", "retro", "nostalgic"), "复古"),
    (("bohemian", "boho"), "波西米亚"),
    (("minimal", "clean line", "simple"), "极简"),
    (("street", "urban", "edgy"), "街头"),
    (("romantic", "feminine", "dainty"), "浪漫"),
    (("elegant", "graceful", "refined"), "优雅"),
    (("chic", "fashion", "trendy", "stylish"), "时尚"),
    (("sporty", "athletic", "active", "workout"), "运动"),
    (("preppy", "campus"), "学院"),
    (("luxury", "designer"), "奢华"),
    (("glam", "glamorous", "evening", "party", "cocktail", "gala"), "华丽"),
)

# (keyword, occasion Chinese, dressiness). The occasion drives the request's
# scene word, which keeps parse_request on the OUTFIT_RECOMMEND path.
_OCCASION_KEYWORDS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("wedding", "bride", "bridesmaid"), "婚礼", "正式"),
    (("gala", "evening", "cocktail", "ball", "opera"), "晚宴", "正式"),
    (("interview",), "面试", "正式"),
    (("business", "office", "corporate", "work", "professional"), "职场", "正式"),
    (("date", "romantic dinner"), "约会", "精致"),
    (("party", "club", "night out"), "派对", "时髦"),
    (("beach", "vacation", "holiday", "resort", "travel", "rio"), "度假", "休闲"),
    (("casual", "weekend", "chill", "street style"), "休闲", "放松"),
    (("sport", "run", "gym", "workout", "athletic", "active"), "运动", "活力"),
    (("spring",), "春日", "清新"),
    (("summer",), "盛夏", "清爽"),
    (("fall", "autumn"), "秋日", "温柔"),
    (("winter", "cozy", "warm"), "冬日", "温暖"),
)

# Colors commonly seen in the official descriptions, for optional color words.
_COLOR_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("black",), "黑色"),
    (("white", "cream"), "白色"),
    (("beige", "nude", "taupe"), "米色"),
    (("gray", "grey"), "灰色"),
    (("navy", "midnight"), "藏蓝"),
    (("blue",), "蓝色"),
    (("red", "burgundy", "bordeaux"), "酒红"),
    (("pink", "blush", "rose"), "粉色"),
    (("purple", "lilac", "plum"), "紫色"),
    (("green", "olive", "forest"), "绿色"),
    (("yellow", "gold", "mustard"), "黄色"),
    (("brown", "tan", "camel", "chocolate"), "棕色"),
    (("silver", "metallic", "platinum"), "银色"),
)

# Word types that mark a follow-up/edit request rather than a fresh brief. The
# drafted request must avoid every one of them so it routes to OUTFIT_RECOMMEND.
# Mirrors workflow/task_workflow.py:42 (_FOLLOW_UP_ADJUST_WORDS).
_FOLLOW_UP_MARKERS = ("更", "再", "别", "不", "一点", "太", "有点", "调整", "改变", "换", "不要", "避免", "避开", "不穿")


@dataclass(frozen=True, slots=True)
class POutfitItem:
    """One normalized p-outfit item with the text the judge will see."""

    item_id: str
    semantic_category: str
    item_type: str
    title: str
    description: str
    url_name: str

    @property
    def name(self) -> str:
        return self.title or self.url_name

    @property
    def search_text(self) -> str:
        return f"{self.title} {self.description} {self.url_name}"

    def to_text_block(self) -> str:
        """Render ``"品类 | 名称 | 描述"`` for the judge's item list."""
        category_zh = item_type_zh(self.item_type)
        description = (self.description or "").strip()
        if description:
            return f"{category_zh} | {self.name.strip()} | {description}"
        return f"{category_zh} | {self.name.strip()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "semantic_category": self.semantic_category,
            "item_type": self.item_type,
            "title": self.title,
            "description": self.description,
            "url_name": self.url_name,
        }


def item_type_zh(item_type: str) -> str:
    return ITEM_TYPE_ZH.get(item_type.strip().lower(), item_type)


def semantic_to_item_type(semantic_category: str) -> str:
    """Coarse semantic_category -> project item_type (slot-safe by construction)."""
    value = semantic_category.strip().lower()
    mapped = SEMANTIC_TO_ITEM_TYPE.get(value, "other")
    if mapped != "other" and infer_slot(mapped) == "other":
        raise ValueError(
            f"semantic_category {semantic_category!r} maps to a slot-less type {mapped!r}"
        )
    return mapped


def refine_item_type(semantic_category: str, text: str) -> str:
    """Bucket-level keyword refinement that never changes the coarse slot."""
    base = semantic_to_item_type(semantic_category)
    lowered = (text or "").lower()
    for keyword, refined in _REFINE_RULES.get(semantic_category.strip().lower(), ()):
        if keyword in lowered:
            if refined not in ALLOWED_ITEM_TYPES:
                raise ValueError(f"refined item_type {refined!r} is not allowed")
            if infer_slot(refined) != infer_slot(base):
                raise ValueError(
                    f"refined item_type {refined!r} changes slot vs {base!r}"
                )
            return refined
    return base


def infer_outfit_slots(items: list[POutfitItem]) -> set[str]:
    """Distinct wardrobe slots present in an outfit (``infer_slot`` on item_type)."""
    return {infer_slot(item.item_type) for item in items}


def outfit_completeness(items: list[POutfitItem]) -> bool:
    """Whether the outfit is a wearable set: separates + shoes, or one-piece + shoes."""
    slots = infer_outfit_slots(items)
    has_separates = {"top", "bottom", "footwear"} <= slots
    has_one_piece = {"one_piece", "footwear"} <= slots
    return has_separates or has_one_piece


# --- file loading -------------------------------------------------------------

def load_item_metadata(path: Path) -> dict[str, dict[str, Any]]:
    """``item_id -> {url_name, description, title, semantic_category, category_id}``."""
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"item metadata must be a JSON object: {path}")
    return payload


def load_outfit_titles(path: Path) -> dict[str, dict[str, str]]:
    """``set_id -> {url_name, title}`` (official outfit-level titles)."""
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"outfit titles must be a JSON object: {path}")
    return payload


def _metadata_item(item_id: str, metadata: dict[str, Any]) -> POutfitItem:
    record = metadata.get(item_id)
    if not isinstance(record, dict):
        raise ValueError(f"item {item_id} has no metadata record")
    semantic = str(record.get("semantic_category", "")).strip().lower()
    if semantic not in SEMANTIC_TO_ITEM_TYPE:
        raise ValueError(f"item {item_id} has unknown semantic_category {semantic!r}")
    title = str(record.get("title", "")).strip()
    description = str(record.get("description", "")).strip()
    url_name = str(record.get("url_name", "")).strip()
    if not url_name:
        raise ValueError(f"item {item_id} has an empty url_name (no text fallback)")
    return POutfitItem(
        item_id=item_id,
        semantic_category=semantic,
        item_type=refine_item_type(semantic, f"{title} {description} {url_name}"),
        title=title,
        description=description,
        url_name=url_name,
    )


def iter_train_outfits(
    train_path: Path,
    metadata: dict[str, Any],
) -> Iterator[dict[str, Any]]:
    """Yield normalized ``{set_id, items}`` outfits from ``nondisjoint/train.json``."""
    with Path(train_path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError(f"train.json must be a JSON array: {train_path}")
    for raw in payload:
        set_id = str(raw.get("set_id", "")).strip()
        raw_items = raw.get("items", [])
        if not set_id or not isinstance(raw_items, list):
            continue
        items: list[POutfitItem] = []
        for raw_item in raw_items:
            item_id = str(raw_item.get("item_id", "")).strip()
            if not item_id:
                continue
            items.append(_metadata_item(item_id, metadata))
        yield {"set_id": set_id, "items": items}


def _outfit_title(outfit: dict[str, Any], titles: dict[str, Any]) -> str:
    record = titles.get(outfit["set_id"])
    if isinstance(record, dict):
        value = str(record.get("title", "")).strip()
        if value:
            return value
    return ""


def select_eval_cases(
    train_path: Path,
    metadata: dict[str, Any],
    titles: dict[str, Any],
    *,
    count: int = 100,
    seed: int = 20260814,
    min_items: int = 4,
) -> list[dict[str, Any]]:
    """Seed-sample complete outfits that carry an English title and enough items.

    Returns self-contained case payloads: ``{id, source_set_id, source_title,
    user_request, golden_item_ids, items, hard_assertions, pass_criteria}``.
    ``user_request`` is a machine-drafted Chinese draft (see
    :func:`draft_user_request`); human review/refinement happens afterwards.
    """
    if count < 1:
        raise ValueError("count must be positive")
    if min_items < 1:
        raise ValueError("min_items must be positive")
    rng = random.Random(seed)
    pool: list[dict[str, Any]] = []
    for outfit in iter_train_outfits(train_path, metadata):
        if len(outfit["items"]) < min_items:
            continue
        if not outfit_completeness(outfit["items"]):
            continue
        title = _outfit_title(outfit, titles)
        if not title:
            continue
        outfit["source_title"] = title
        pool.append(outfit)
    rng.shuffle(pool)

    cases: list[dict[str, Any]] = []
    for index, outfit in enumerate(pool[:count], start=1):
        items = outfit["items"]
        cases.append(
            {
                "id": f"poutfit-{index:04d}",
                "source_set_id": outfit["set_id"],
                "source_title": outfit["source_title"],
                "user_request": draft_user_request(outfit),
                "golden_item_ids": [item.item_id for item in items],
                "items": [item.to_dict() for item in items],
                "hard_assertions": ["all_items_in_wardrobe", "has_required_slots"],
                "pass_criteria": {"min_judge_overall": 60},
            }
        )
    return cases


# --- request drafting ---------------------------------------------------------

def _first_descriptor(text: str, table: tuple[tuple[tuple[str, ...], str], ...]) -> list[str]:
    lowered = text.lower()
    found: list[str] = []
    for keywords, descriptor in table:
        if any(keyword in lowered for keyword in keywords):
            found.append(descriptor)
    return found


def _item_type(item: POutfitItem | dict[str, Any]) -> str:
    return item.item_type if isinstance(item, POutfitItem) else str(item.get("item_type", "other"))


def _item_search_text(item: POutfitItem | dict[str, Any]) -> str:
    if isinstance(item, POutfitItem):
        return item.search_text
    return " ".join(
        str(item.get(key, ""))
        for key in ("title", "description", "url_name")
        if str(item.get(key, "")).strip()
    )


def _slot_brief(items: list[POutfitItem]) -> str:
    """A Chinese category skeleton for the outfit's core pieces."""
    one_piece_types = [
        _item_type(item) for item in items if infer_slot(_item_type(item)) == "one_piece"
    ]
    if one_piece_types:
        # Prefer the more specific type label (jumpsuit/suit) over dress.
        ordered = sorted(
            dict.fromkeys(one_piece_types),
            key=lambda value: (0 if value in {"jumpsuit", "suit"} else 1, value),
        )
        return item_type_zh(ordered[0])
    top_types = sorted(
        {_item_type(item) for item in items if infer_slot(_item_type(item)) == "top"}
    )
    bottom_types = sorted(
        {_item_type(item) for item in items if infer_slot(_item_type(item)) == "bottom"}
    )
    top_label = item_type_zh(top_types[0]) if top_types else "上衣"
    bottom_label = item_type_zh(bottom_types[0]) if bottom_types else "下装"
    return f"{top_label}和{bottom_label}"


def draft_user_request(outfit: dict[str, Any]) -> str:
    """Reverse-draft a fresh-brief Chinese request from the outfit's title+items.

    The draft follows the case-file rule: it contains a scene word, names only
    categories the golden outfit actually carries, and avoids every follow-up
    marker (``更/再/别/一点/换/不要/避免``). It is a starting point that gets
    human-refined before the case set is frozen.
    """
    title = outfit.get("source_title", "") or outfit["source_title"]
    items = outfit["items"]
    combined = f"{title} " + " ".join(_item_search_text(item) for item in items)

    scene, dressiness = "日常", ""
    for keywords, occasion, tone in _OCCASION_KEYWORDS:
        if any(keyword in combined.lower() for keyword in keywords):
            scene, dressiness = occasion, tone
            break

    styles = _first_descriptor(combined, _STYLE_KEYWORDS)
    colors = _first_descriptor(combined, _COLOR_KEYWORDS)
    style_text = "、".join(list(dict.fromkeys(styles))[:2]) if styles else ""
    color_text = colors[0] if colors else ""

    brief = _slot_brief(items)

    parts: list[str] = []
    if dressiness:
        parts.append(dressiness)
    if style_text:
        parts.append(style_text)
    if color_text:
        parts.append(color_text)
    mood = "、".join(dict.fromkeys(parts)) or "耐看"

    # Templates rotate so the 100 cases are not verbatim duplicates. All are
    # fresh briefs (no follow-up markers) with an explicit scene word, and kept
    # around 30-45 Chinese characters so the request stays specific (the five
    # dimensions need real signal, not padded prose).
    template_index = random.Random(f"draft-{outfit['set_id']}").randrange(4)
    if template_index == 0:
        request = f"我想为{scene}准备一套穿搭，{mood}，主打{brief}，配好鞋子。"
    elif template_index == 1:
        request = f"帮我搭一套{scene}造型，{mood}是重点，单品围绕{brief}，整体协调完整。"
    elif template_index == 2:
        request = f"想要一套适合{scene}的穿搭，以{brief}为核心，风格走{mood}，整套配齐鞋履。"
    else:
        request = f"请推荐一套{scene}穿搭，{mood}，以{brief}为主体，搭配合适鞋履。"
    return request


# --- DB normalization ----------------------------------------------------------

def normalize_p_outfit_item(item: POutfitItem, *, mode: str) -> CatalogItem:
    """A CatalogItem for the eval wardrobe snapshot (item_id keeps the raw id).

    ``mode`` switches the image story: ``order`` (order-import simulation) is
    image-less with ``unbound`` status and no image fields; ``image`` (photo
    import simulation) binds ``images/nondisjoint/train/{id}.jpg`` as available.
    """
    if mode == "order":
        image_status, image_filename, relative_path = ImageStatus.UNBOUND, "", ""
    elif mode == "image":
        image_status = ImageStatus.AVAILABLE
        image_filename = f"{item.item_id}.jpg"
        relative_path = f"nondisjoint/train/{item.item_id}.jpg"
    else:
        raise ValueError(f"unknown p-outfit mode: {mode!r}")
    return CatalogItem(
        item_id=item.item_id,
        source="polyvore",
        gender="unknown",
        item_type=item.item_type,
        main_category=item.semantic_category,
        name=item.name,
        color="",
        description=item.description,
        features=(),
        image_filename=image_filename,
        relative_image_path=relative_path,
        image_status=image_status,
        embedding_status=EmbeddingStatus.PENDING,
        dataset_item_id="",
    )
