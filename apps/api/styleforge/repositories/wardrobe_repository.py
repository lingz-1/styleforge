"""User wardrobe persistence backed by catalog item references."""

from __future__ import annotations

import json
from styleforge.repositories.database import Connection
from collections.abc import Iterable
from datetime import datetime, timezone

from styleforge.core.categories import infer_slot
from styleforge.core.schemas import CatalogItem, EmbeddingStatus, ImageStatus


def add_items(
    connection: Connection,
    user_id: str,
    item_ids: Iterable[str],
) -> int:
    unique_ids = tuple(dict.fromkeys(item_ids))
    if not unique_ids:
        return 0
    placeholders = ",".join("%s" for _ in unique_ids)
    existing_ids = {
        row[0]
        for row in connection.execute(
            f"SELECT item_id FROM catalog_items WHERE item_id IN ({placeholders})",  # noqa: S608
            unique_ids,
        )
    }
    missing = set(unique_ids) - existing_ids
    if missing:
        sample = ", ".join(sorted(missing)[:5])
        raise ValueError(f"Catalog item IDs not found: {sample}")
    added_at = datetime.now(timezone.utc).isoformat()
    connection.executemany(
        """
        INSERT INTO wardrobe_items(user_id, item_id, active, added_at)
        VALUES (%s, %s, 1, %s)
        ON CONFLICT(user_id, item_id) DO UPDATE SET active = 1
        """,
        ((user_id, item_id, added_at) for item_id in unique_ids),
    )
    return len(unique_ids)


def deactivate_all_items(connection: Connection, user_id: str) -> int:
    cursor = connection.execute(
        "UPDATE wardrobe_items SET active = 0 WHERE user_id = %s AND active = 1",
        (user_id,),
    )
    return cursor.rowcount


def list_items(connection: Connection, user_id: str) -> list[CatalogItem]:
    rows = connection.execute(
        """
        SELECT catalog_items.*
        FROM wardrobe_items
        JOIN catalog_items USING(item_id)
        WHERE wardrobe_items.user_id = %s AND wardrobe_items.active = 1
        ORDER BY catalog_items.item_id
        """,
        (user_id,),
    )
    return [
        CatalogItem(
            item_id=row["item_id"],
            source=row["source"],
            gender=row["gender"],
            item_type=row["item_type"],
            main_category=row["main_category"],
            name=row["name"],
            color=row["color"],
            description=row["description"],
            features=tuple(json.loads(row["features_json"])),
            image_filename=row["image_filename"],
            relative_image_path=row["relative_image_path"],
            image_status=ImageStatus(row["image_status"]),
            embedding_status=EmbeddingStatus(row["embedding_status"]),
            raw_json_hash=row["raw_json_hash"],
        )
        for row in rows
    ]


def choose_demo_items(
    connection: Connection,
    item_types: Iterable[str],
    per_type: int,
) -> list[str]:
    if per_type < 1:
        raise ValueError("per_type must be positive")
    chosen: list[str] = []
    for item_type in item_types:
        chosen.extend(
            row[0]
            for row in connection.execute(
                """
                SELECT item_id FROM catalog_items
                WHERE item_type = %s AND name <> '' AND color <> ''
                ORDER BY item_id
                LIMIT %s
                """,
                (item_type, per_type),
            )
        )
    return chosen


def choose_demo_outfit_items(
    connection: Connection,
    outfit_count: int,
    split: str = "train",
) -> tuple[list[str], list[str]]:
    """Select complete real outfits so the demo wardrobe has known positive combinations."""
    if outfit_count < 1:
        raise ValueError("outfit_count must be positive")
    rows = connection.execute(
        """
        SELECT o.outfit_id, oi.item_id, c.item_type
        FROM dataset_outfits AS o
        JOIN dataset_outfit_items AS oi ON oi.outfit_id = o.outfit_id
        JOIN catalog_items AS c ON c.item_id = oi.item_id
        WHERE o.split = %s
        ORDER BY o.outfit_id, oi.position
        """,
        (split,),
    )
    selected_outfits: list[str] = []
    selected_items: list[str] = []
    current_outfit: str | None = None
    current_items: list[str] = []
    current_slots: set[str] = set()

    def accept_current() -> None:
        if current_outfit is None or len(selected_outfits) >= outfit_count:
            return
        has_separates = {"top", "bottom", "footwear"}.issubset(current_slots)
        has_one_piece = {"one_piece", "footwear"}.issubset(current_slots)
        if has_separates or has_one_piece:
            selected_outfits.append(current_outfit)
            selected_items.extend(current_items)

    for row in rows:
        outfit_id = row["outfit_id"]
        if current_outfit is not None and outfit_id != current_outfit:
            accept_current()
            if len(selected_outfits) >= outfit_count:
                break
            current_items = []
            current_slots = set()
        current_outfit = outfit_id
        current_items.append(row["item_id"])
        current_slots.add(infer_slot(row["item_type"]))
    else:
        accept_current()

    return list(dict.fromkeys(selected_items)), selected_outfits
