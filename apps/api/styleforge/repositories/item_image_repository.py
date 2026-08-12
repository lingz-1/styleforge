"""Persistence for product, detail, and model images."""

from __future__ import annotations

from styleforge.repositories.database import Connection
from collections.abc import Sequence

from styleforge.core.schemas import CatalogItemImage


UPSERT_IMAGE_SQL = """
INSERT INTO catalog_item_images (
    item_id, position, image_role, image_filename, relative_image_path,
    image_status, is_primary
) VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT(item_id, position) DO UPDATE SET
    image_role = excluded.image_role,
    image_filename = excluded.image_filename,
    relative_image_path = excluded.relative_image_path,
    image_status = excluded.image_status,
    is_primary = excluded.is_primary
"""


def replace_item_images(
    connection: Connection,
    images: Sequence[CatalogItemImage],
) -> int:
    item_ids = tuple(dict.fromkeys(image.item_id for image in images))
    if not item_ids:
        return 0
    connection.executemany(
        "DELETE FROM catalog_item_images WHERE item_id = %s",
        ((item_id,) for item_id in item_ids),
    )
    connection.executemany(
        UPSERT_IMAGE_SQL,
        (
            (
                image.item_id,
                image.position,
                image.image_role,
                image.image_filename,
                image.relative_image_path,
                image.image_status.value,
                int(image.is_primary),
            )
            for image in images
        ),
    )
    return len(images)
