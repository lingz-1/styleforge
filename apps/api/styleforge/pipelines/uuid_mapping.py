"""Lookup-or-create helpers for UUID-based catalog item ids.

Every item entering the catalog gets a random UUID as its ``item_id`` while
the raw dataset id is preserved in ``dataset_item_id``. Re-running an import
pipeline must converge to the same UUID for the same ``(source,
dataset_item_id)`` pair, so pipelines keep a ``raw_to_uuid`` dict and fill it
from the database before minting fresh UUIDs.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any

from styleforge.repositories.catalog_repository import existing_dataset_item_ids


def ensure_uuids(
    connection: Any,
    source: str,
    raw_to_uuid: dict[str, str],
    raw_ids: list[str],
) -> None:
    """Populate ``raw_to_uuid`` for any raw ids not yet mapped.

    Reuses the existing UUID for ``(source, dataset_item_id)`` pairs already in
    the catalog (idempotent replay) and mints a fresh UUID otherwise.
    """
    missing = [raw for raw in raw_ids if raw not in raw_to_uuid]
    if not missing:
        return
    raw_to_uuid.update(existing_dataset_item_ids(connection, source, missing))
    for raw in missing:
        if raw not in raw_to_uuid:
            raw_to_uuid[raw] = str(uuid.uuid4())


def reid_catalog_item(item: Any, raw_to_uuid: dict[str, str]) -> Any:
    """Return a CatalogItem whose item_id is its UUID and dataset_item_id the raw id.

    ``item.item_id`` still holds the raw dataset id at this point (normalizers
    build it from the metadata), so it is used as the ``dataset_item_id``.
    """
    raw = item.item_id
    return replace(item, item_id=raw_to_uuid[raw], dataset_item_id=raw)


def reid_images(images: list[Any], raw_to_uuid: dict[str, str]) -> list[Any]:
    """Remap catalog_item_images' item_id to the new UUIDs."""
    return [replace(img, item_id=raw_to_uuid[img.item_id]) for img in images]
