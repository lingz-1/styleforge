"""Validate, store, and embed user-provided wardrobe images."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.wardrobe_import_repository import (
    personal_image_root,
)
from styleforge.services.personal_embeddings import embed_personal_items


MAX_IMAGE_BYTES = 20 * 1024 * 1024


def save_personal_image(root: Path, item_id: str, image_bytes: bytes) -> str:
    """Validate image bytes, save a downscaled JPEG, return the stored filename."""
    if not image_bytes:
        raise ValueError("Image is empty")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds the 20 MB limit")
    root.mkdir(parents=True, exist_ok=True)
    filename = f"{hashlib.sha256(item_id.encode('utf-8')).hexdigest()[:24]}.jpg"
    destination = (root / filename).resolve()
    if not destination.is_relative_to(root):
        raise ValueError("Invalid personal image destination")

    handle = tempfile.NamedTemporaryFile(
        dir=root,
        prefix=".wardrobe-image-",
        suffix=".jpg.tmp",
        delete=False,
    )
    temporary_path = Path(handle.name)
    handle.close()
    try:
        import io
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as image:
            image.load()
            rgb = image.convert("RGB")
            try:
                rgb.thumbnail((2400, 2400))
                rgb.save(temporary_path, format="JPEG", quality=92, optimize=True)
            finally:
                rgb.close()
        os.replace(temporary_path, destination)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return filename


def bind_personal_image(
    *,
    database_path: Path,
    artifact_root: Path,
    user_id: str,
    item_id: str,
    image_bytes: bytes,
    model_dir: Path,
    device: str = "cuda",
) -> dict[str, Any]:
    if not image_bytes:
        raise ValueError("Image is empty")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds the 20 MB limit")
    initialize_database(database_path)
    with database_session(database_path) as connection:
        row = connection.execute(
            """
            SELECT c.item_id, c.source
            FROM catalog_items AS c
            JOIN personal_wardrobe_items AS p ON p.item_id = c.item_id
            WHERE c.item_id = ? AND p.user_id = ?
            """,
            (item_id, user_id),
        ).fetchone()
    if row is None:
        raise ValueError("Personal wardrobe item not found")

    root = personal_image_root(artifact_root, user_id)
    filename = save_personal_image(root, item_id, image_bytes)

    with database_session(database_path) as connection:
        connection.execute(
            """
            UPDATE catalog_items
            SET image_filename = ?, relative_image_path = ?, image_status = 'available',
                embedding_status = 'pending'
            WHERE item_id = ?
            """,
            (filename, filename, item_id),
        )
        connection.execute(
            """
            INSERT INTO catalog_item_images(
                item_id, position, image_role, image_filename,
                relative_image_path, image_status, is_primary
            ) VALUES (?, 0, 'primary', ?, ?, 'available', 1)
            ON CONFLICT(item_id, position) DO UPDATE SET
                image_filename = excluded.image_filename,
                relative_image_path = excluded.relative_image_path,
                image_status = 'available',
                is_primary = 1
            """,
            (item_id, filename, filename),
        )

    try:
        embedding = embed_personal_items(
            database_path=database_path,
            item_ids=[item_id],
            model_dir=model_dir,
            device=device,
            batch_size=1,
        )
    except BaseException as error:
        embedding = {
            "status": "failed",
            "error_type": type(error).__name__,
            "error": str(error),
        }
    return {
        "item_id": item_id,
        "image_status": "available",
        "relative_image_path": filename,
        "embedding": embedding,
    }
