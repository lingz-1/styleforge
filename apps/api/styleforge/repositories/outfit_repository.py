"""Dataset outfit persistence operations."""

from __future__ import annotations

import json
from styleforge.repositories.database import Connection
from collections.abc import Sequence
from datetime import datetime, timezone

from styleforge.data.outfits import DatasetOutfit


UPSERT_OUTFIT_SQL = """
INSERT INTO dataset_outfits (
    outfit_id, source, split, gender, name, description, style, season, occasion,
    theme, color_palette_json, is_official_outfit, is_official_look,
    source_revision, imported_at
) VALUES (%s, %s, %s, %s, '', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT(outfit_id) DO UPDATE SET
    source = excluded.source,
    split = excluded.split,
    gender = excluded.gender,
    description = excluded.description,
    style = excluded.style,
    season = excluded.season,
    occasion = excluded.occasion,
    theme = excluded.theme,
    color_palette_json = excluded.color_palette_json,
    is_official_outfit = excluded.is_official_outfit,
    is_official_look = excluded.is_official_look,
    source_revision = excluded.source_revision,
    imported_at = excluded.imported_at
"""


def upsert_outfits(
    connection: Connection,
    outfits: Sequence[DatasetOutfit],
    source_revision: str,
) -> tuple[int, int]:
    if not outfits:
        return 0, 0
    imported_at = datetime.now(timezone.utc).isoformat()
    connection.executemany(
        UPSERT_OUTFIT_SQL,
        (
            (
                outfit.outfit_id,
                outfit.source,
                outfit.split,
                outfit.gender,
                outfit.description,
                outfit.style,
                outfit.season,
                outfit.occasion,
                outfit.theme,
                json.dumps(outfit.color_palette, ensure_ascii=False),
                int(outfit.is_official_outfit),
                int(outfit.is_official_look),
                source_revision,
                imported_at,
            )
            for outfit in outfits
        ),
    )
    outfit_ids = tuple(outfit.outfit_id for outfit in outfits)
    placeholders = ",".join("%s" for _ in outfit_ids)
    connection.execute(
        f"DELETE FROM dataset_outfit_items WHERE outfit_id IN ({placeholders})",  # noqa: S608
        outfit_ids,
    )
    item_rows = [
        (outfit.outfit_id, position, item_id, description)
        for outfit in outfits
        for position, (item_id, description) in enumerate(outfit.items)
    ]
    connection.executemany(
        """
        INSERT INTO dataset_outfit_items(outfit_id, position, item_id, item_description)
        VALUES (%s, %s, %s, %s)
        """,
        item_rows,
    )
    return len(outfits), len(item_rows)


def outfit_counts(connection: Connection) -> dict[str, object]:
    outfit_count = connection.execute("SELECT COUNT(*) FROM dataset_outfits").fetchone()[0]
    relation_count = connection.execute("SELECT COUNT(*) FROM dataset_outfit_items").fetchone()[0]
    by_split = {
        row["split"]: row["count"]
        for row in connection.execute(
            "SELECT split, COUNT(*) AS count FROM dataset_outfits "
            "GROUP BY split ORDER BY count DESC"
        )
    }
    return {
        "dataset_outfits": outfit_count,
        "dataset_outfit_items": relation_count,
        "by_split": by_split,
    }

