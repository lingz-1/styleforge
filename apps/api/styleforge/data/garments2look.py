"""Garments2Look-Polyvore metadata normalization and deferred image binding."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from styleforge.core.schemas import CatalogItem, EmbeddingStatus, ImageBinding, ImageStatus


REQUIRED_FIELDS = ("source", "gender", "type", "name", "main_category")


def _clean_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _safe_path_segment(value: Any, fallback: str) -> str:
    cleaned = _clean_text(value).replace("\\", "/")
    segment = PurePosixPath(cleaned).name
    return segment if segment not in {"", ".", ".."} else fallback


def product_image_filename(item_id: str, record: dict[str, Any]) -> str:
    images = record.get("images")
    if isinstance(images, dict):
        product = images.get("product")
        if isinstance(product, dict):
            full = product.get("full")
            if isinstance(full, list):
                for filename in full:
                    cleaned = _clean_text(filename)
                    if cleaned:
                        return Path(cleaned).name
    return f"{item_id}.jpg"


class ImagePathResolver:
    """Resolve dataset-relative paths now and bind an absolute root later."""

    def __init__(self, image_root: Path | None = None) -> None:
        self.image_root = image_root.resolve() if image_root is not None else None

    def relative_path(self, item_id: str, record: dict[str, Any]) -> str:
        gender = _safe_path_segment(record.get("gender"), "unknown_gender")
        item_type = _safe_path_segment(record.get("type"), "unknown_type")
        filename = product_image_filename(item_id, record)
        return PurePosixPath(gender, item_type, filename).as_posix()

    def bind(self, relative_path: str) -> ImageBinding:
        if self.image_root is None:
            return ImageBinding(relative_path, None, ImageStatus.UNBOUND)
        absolute_path = self.image_root.joinpath(*PurePosixPath(relative_path).parts)
        status = ImageStatus.AVAILABLE if absolute_path.is_file() else ImageStatus.MISSING
        return ImageBinding(relative_path, absolute_path, status)


def normalize_catalog_item(
    item_id: str,
    record: dict[str, Any],
    resolver: ImagePathResolver,
) -> CatalogItem:
    if not isinstance(record, dict):
        raise TypeError(f"Record {item_id!r} must be an object")

    relative_path = resolver.relative_path(item_id, record)
    binding = resolver.bind(relative_path)
    raw_hash = hashlib.sha256(
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    raw_features = record.get("features")
    features = (
        tuple(_clean_text(value) for value in raw_features if _clean_text(value))
        if isinstance(raw_features, list)
        else ()
    )

    return CatalogItem(
        item_id=item_id,
        source=_clean_text(record.get("source")) or "polyvore",
        gender=_clean_text(record.get("gender")) or "unknown",
        item_type=_clean_text(record.get("type")) or "unknown",
        main_category=_clean_text(record.get("main_category")) or "unknown",
        name=_clean_text(record.get("name")),
        color=_clean_text(record.get("color")),
        description=_clean_text(record.get("description")),
        features=features,
        image_filename=product_image_filename(item_id, record),
        relative_image_path=relative_path,
        image_status=binding.status,
        embedding_status=EmbeddingStatus.PENDING,
        raw_json_hash=raw_hash,
    )
