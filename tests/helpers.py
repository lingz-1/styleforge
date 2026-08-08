"""Shared in-memory factories for tests (no disk, no GPU)."""

from __future__ import annotations

from styleforge.core.schemas import CatalogItem, EmbeddingStatus, ImageStatus


def make_item(
    item_id: str,
    item_type: str,
    name: str = "",
    color: str = "",
    gender: str = "women",
    source: str = "polyvore",
    features: tuple[str, ...] = (),
    description: str = "",
) -> CatalogItem:
    return CatalogItem(
        item_id=item_id,
        source=source,
        gender=gender,
        item_type=item_type,
        main_category=item_type,
        name=name,
        color=color,
        description=description,
        features=tuple(features),
        image_filename="",
        relative_image_path="",
        image_status=ImageStatus.UNBOUND,
        embedding_status=EmbeddingStatus.PENDING,
    )
