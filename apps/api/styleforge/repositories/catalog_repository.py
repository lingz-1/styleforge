"""Catalog persistence operations."""

from __future__ import annotations

import json
from styleforge.repositories.database import Connection, Row
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone

from styleforge.core.schemas import CatalogItem


UPSERT_ITEM_SQL = """
INSERT INTO catalog_items (
    item_id, source, gender, item_type, main_category, name, color, description,
    features_json, image_filename, relative_image_path, image_status,
    embedding_status, raw_json_hash, source_revision, imported_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT(item_id) DO UPDATE SET
    source = excluded.source,
    gender = excluded.gender,
    item_type = excluded.item_type,
    main_category = excluded.main_category,
    name = excluded.name,
    color = excluded.color,
    description = excluded.description,
    features_json = excluded.features_json,
    image_filename = excluded.image_filename,
    relative_image_path = excluded.relative_image_path,
    image_status = excluded.image_status,
    embedding_status = CASE
        WHEN catalog_items.raw_json_hash = excluded.raw_json_hash
        THEN catalog_items.embedding_status
        ELSE 'pending'
    END,
    raw_json_hash = excluded.raw_json_hash,
    source_revision = excluded.source_revision,
    imported_at = excluded.imported_at
"""


def _item_row(item: CatalogItem, source_revision: str, imported_at: str) -> tuple[object, ...]:
    return (
        item.item_id,
        item.source,
        item.gender,
        item.item_type,
        item.main_category,
        item.name,
        item.color,
        item.description,
        json.dumps(item.features, ensure_ascii=False),
        item.image_filename,
        item.relative_image_path,
        item.image_status.value,
        item.embedding_status.value,
        item.raw_json_hash,
        source_revision,
        imported_at,
    )


def upsert_items(
    connection: Connection,
    items: Sequence[CatalogItem],
    source_revision: str,
) -> int:
    if not items:
        return 0
    imported_at = datetime.now(timezone.utc).isoformat()
    connection.executemany(
        UPSERT_ITEM_SQL,
        (_item_row(item, source_revision, imported_at) for item in items),
    )
    return len(items)


def catalog_counts(connection: Connection) -> dict[str, object]:
    total = connection.execute("SELECT COUNT(*) FROM catalog_items").fetchone()[0]
    by_type = {
        row["item_type"]: row["count"]
        for row in connection.execute(
            "SELECT item_type, COUNT(*) AS count FROM catalog_items "
            "GROUP BY item_type ORDER BY count DESC, item_type"
        )
    }
    by_image_status = {
        row["image_status"]: row["count"]
        for row in connection.execute(
            "SELECT image_status, COUNT(*) AS count FROM catalog_items "
            "GROUP BY image_status ORDER BY image_status"
        )
    }
    return {
        "catalog_items": total,
        "by_type": by_type,
        "by_image_status": by_image_status,
    }


def fetch_items_by_ids(
    connection: Connection, item_ids: Iterable[str]
) -> list[Row]:
    ids = tuple(dict.fromkeys(item_ids))
    if not ids:
        return []
    placeholders = ",".join("%s" for _ in ids)
    return list(
        connection.execute(
            f"SELECT * FROM catalog_items WHERE item_id IN ({placeholders})",  # noqa: S608
            ids,
        )
    )

