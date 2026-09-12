"""Normalize Garments2Look-Mytheresa products and multi-view image metadata."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from styleforge.core.categories import infer_slot
from styleforge.core.schemas import (
    CatalogItem,
    CatalogItemImage,
    EmbeddingStatus,
    ImageStatus,
)


IMAGE_GROUPS = (
    ("product_full", "product", "full"),
    ("product_partial", "product", "partial"),
    ("model_full", "model", "full"),
    ("model_partial", "model", "partial"),
)


def _clean_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _filename(value: Any) -> str:
    cleaned = _clean_text(value).replace("\\", "/")
    name = PurePosixPath(cleaned).name
    return name if name not in {"", ".", ".."} else ""


def canonical_item_type(record: dict[str, Any]) -> str:
    """Map Mytheresa's hierarchy to StyleForge's recommendation vocabulary."""
    raw_type = _clean_text(record.get("type")).lower()
    main_category = _clean_text(record.get("main_category")).lower()

    if main_category == "shoes" or raw_type.startswith("shoes::"):
        return "shoes"
    if main_category == "bag" or raw_type.startswith("bags::"):
        return "bag"

    if main_category.startswith("accessory::"):
        if "earring" in raw_type or main_category == "accessory::ear":
            return "earrings"
        if "necklace" in raw_type:
            return "necklace"
        if "scarf" in raw_type or "shawl" in raw_type:
            return "neckwear"
        if "watch" in raw_type:
            return "watches"
        if "bracelet" in raw_type or "bangle" in raw_type:
            return "bracelet"
        if "ring" in raw_type or main_category == "accessory::finger":
            return "rings"
        if "sunglass" in raw_type or "eyeglass" in raw_type:
            return "eyewear"
        if "belt" in raw_type or main_category == "accessory::waist":
            return "belts"
        if "glove" in raw_type or main_category == "accessory::hand":
            return "gloves"
        if "hair" in raw_type or main_category == "accessory::hair":
            return "hairwear"
        if "hat" in raw_type or "cap" in raw_type or main_category == "accessory::head":
            return "hats"
        if main_category == "accessory::leg":
            return "legwear"
        if main_category == "accessory::chest":
            return "brooch"
        return "jewellery"

    if "ski mask" in raw_type:
        return "hats"
    if "legwarmer" in raw_type:
        return "legwear"
    if "base layers upper" in raw_type:
        return "base_layer_top"
    if "base layers lower" in raw_type:
        return "base_layer_bottom"
    if "skiwear" in raw_type:
        return "skiwear"
    if "activewear::bras" in raw_type:
        return "activewear_bra"
    if "beachwear::bikinis bottoms" in raw_type:
        return "swim_bottom"
    if "beachwear::cover-ups" in raw_type:
        return "swim_coverup"
    if (
        "beachwear::swimsuits" in raw_type
        or "beachwear::bikinis sets" in raw_type
        or "girls' swimwear" in raw_type
        or "boys' swimwear" in raw_type
        or "baby swimwear" in raw_type
        or raw_type == "clothing::swimwear"
    ):
        return "swimwear"
    if "bathtime" in raw_type:
        return "bathwear"
    if "nightwear" in raw_type or "pyjama" in raw_type or "pajama" in raw_type:
        return "sleepwear"
    if (
        "underwear" in raw_type
        or "lingerie" in raw_type
        or "boxers" in raw_type
        or "briefs" in raw_type
        or "bralette" in raw_type
    ):
        return "underwear"
    if (
        raw_type == "baby outfits"
        or "girls' outfits" in raw_type
        or "boys' outfits" in raw_type
    ):
        return "outfit_set"
    if "clothing::tailoring::" in raw_type:
        return "suit"

    if "dress" in raw_type or "gown" in raw_type:
        return "dress"
    if "jumpsuit" in raw_type or "playsuit" in raw_type or "romper" in raw_type:
        return "jumpsuit"
    if "suit" in raw_type and "swimsuit" not in raw_type:
        return "suit"
    if "skirt" in raw_type:
        return "skirt"
    if "shorts" in raw_type:
        return "shorts"
    if any(word in raw_type for word in ("pants", "trousers", "jeans", "leggings")):
        return "pants"
    if any(word in raw_type for word in ("coat", "jacket", "blazer", "outerwear")):
        return "outwear"
    if any(
        word in raw_type
        for word in ("tops", "shirt", "blouse", "knitwear", "sweater", "cardigan", "hoodie")
    ):
        return "top"
    return "other"


class MytheresaImagePathResolver:
    """Bind `<item_id>/<filename>` paths to an optional external root."""

    def __init__(self, image_root: Path | None = None) -> None:
        self.image_root = image_root.resolve() if image_root is not None else None

    def relative_path(self, item_id: str, filename: str) -> str:
        safe_item_id = _filename(item_id)
        safe_filename = _filename(filename)
        if not safe_item_id or not safe_filename:
            raise ValueError(f"Invalid Mytheresa image path: {item_id!r}/{filename!r}")
        return PurePosixPath(safe_item_id, safe_filename).as_posix()

    def status(self, relative_path: str) -> ImageStatus:
        if self.image_root is None:
            return ImageStatus.UNBOUND
        absolute_path = self.image_root.joinpath(*PurePosixPath(relative_path).parts)
        return ImageStatus.AVAILABLE if absolute_path.is_file() else ImageStatus.MISSING


def _iter_image_metadata(
    item_id: str,
    record: dict[str, Any],
    resolver: MytheresaImagePathResolver,
) -> list[CatalogItemImage]:
    images = record.get("images")
    if not isinstance(images, dict):
        return []
    result: list[CatalogItemImage] = []
    seen: set[str] = set()
    for role, group_name, size_name in IMAGE_GROUPS:
        group = images.get(group_name)
        filenames = group.get(size_name) if isinstance(group, dict) else None
        if not isinstance(filenames, list):
            continue
        for raw_filename in filenames:
            filename = _filename(raw_filename)
            if not filename:
                continue
            relative_path = resolver.relative_path(item_id, filename)
            if relative_path in seen:
                continue
            seen.add(relative_path)
            result.append(
                CatalogItemImage(
                    item_id=item_id,
                    position=len(result),
                    image_role=role,
                    image_filename=filename,
                    relative_image_path=relative_path,
                    image_status=resolver.status(relative_path),
                    is_primary=not result,
                )
            )
    return result


def normalize_mytheresa_item(
    item_id: str,
    record: dict[str, Any],
    resolver: MytheresaImagePathResolver,
) -> tuple[CatalogItem, tuple[CatalogItemImage, ...]]:
    if not isinstance(record, dict):
        raise TypeError(f"Record {item_id!r} must be an object")
    images = _iter_image_metadata(item_id, record, resolver)
    if not images:
        raise ValueError(f"Record {item_id!r} has no usable images")

    raw_type = _clean_text(record.get("type"))
    raw_features = record.get("features")
    features = [
        _clean_text(value)
        for value in raw_features
        if _clean_text(value)
    ] if isinstance(raw_features, list) else []
    if raw_type:
        features.append(f"Dataset category: {raw_type}")

    descriptions = []
    for key in ("official_description", "description"):
        value = _clean_text(record.get(key))
        if value and value not in descriptions:
            descriptions.append(value)

    raw_hash = hashlib.sha256(
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    item_type = canonical_item_type(record)
    primary = images[0]
    item = CatalogItem(
        item_id=item_id,
        source="mytheresa",
        gender=_clean_text(record.get("gender")) or "unknown",
        item_type=item_type,
        main_category=infer_slot(item_type),
        name=_clean_text(record.get("name")),
        color=_clean_text(record.get("color")),
        description="\n".join(descriptions),
        features=tuple(features),
        image_filename=primary.image_filename,
        relative_image_path=primary.relative_image_path,
        image_status=primary.image_status,
        embedding_status=EmbeddingStatus.PENDING,
        raw_json_hash=raw_hash,
    )
    return item, tuple(images)
