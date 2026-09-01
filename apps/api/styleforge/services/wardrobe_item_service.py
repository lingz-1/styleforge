"""Personal wardrobe item lifecycle: create-from-photo and metadata updates."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from styleforge.core.categories import ALLOWED_ITEM_TYPES, infer_slot
from styleforge.core.schemas import CatalogItem, EmbeddingStatus, ImageStatus
from styleforge.repositories.catalog_repository import upsert_items
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.dataset_source_repository import register_dataset_source
from styleforge.repositories.personal_embedding_repository import (
    invalidate_personal_embeddings,
    mark_personal_embedding_failed,
)
from styleforge.repositories.wardrobe_import_repository import (
    personal_image_root,
    personal_source_for_user,
)
from styleforge.services.personal_embeddings import embed_personal_items
from styleforge.services.personal_images import save_personal_image


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _item_to_dict(connection, item_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM catalog_items WHERE item_id = %s", (item_id,)
    ).fetchone()
    if row is None:
        return None
    return {
        "item_id": row["item_id"],
        "source": row["source"],
        "gender": row["gender"],
        "item_type": row["item_type"],
        "main_category": row["main_category"],
        "name": row["name"],
        "color": row["color"],
        "description": row["description"],
        "image_status": row["image_status"],
        "embedding_status": row["embedding_status"],
        "attributes": json.loads(row["attributes_json"] or "{}"),
        "image_url": f"/items/{row['item_id']}/image",
    }


def create_photo_item(
    *,
    database_path: str,
    artifact_root: Path,
    user_id: str,
    image_bytes: bytes,
    model_dir: Path,
    device: str = "cuda",
    name: str = "",
    item_type: str = "",
    subtype: str = "",
    color: str = "",
    gender: str = "women",
    size: str = "",
    attributes: dict | None = None,
    item_id: str | None = None,
    skip_embedding: bool = False,
    skip_initialize: bool = False,
) -> dict[str, Any]:
    """Create a new personal wardrobe item from a user photo.

    ``skip_embedding`` leaves the item at ``embedding_status = 'pending'``
    (batch recognition imports many items at once and must not race for the
    GPU embedding encoder). ``skip_initialize`` assumes the caller already
    initialized the schema, avoiding repeated DDL under concurrency.
    """
    if not item_type.strip():
        raise ValueError("item_type is required")
    item_type = item_type.strip()
    if item_type not in ALLOWED_ITEM_TYPES:
        raise ValueError(f"Unsupported item_type: {item_type}")

    if not skip_initialize:
        initialize_database(database_path)
    item_id = item_id or str(uuid.uuid4())
    source = personal_source_for_user(user_id)
    root = personal_image_root(artifact_root, user_id)
    filename = save_personal_image(root, item_id, image_bytes)

    features = tuple(
        value
        for value in (subtype.strip(), f"size:{size.strip()}" if size.strip() else "")
        if value
    )
    ai_description = (attributes or {}).get("description") or ""
    item = CatalogItem(
        item_id=item_id,
        source=source,
        gender=gender or "women",
        item_type=item_type,
        main_category=infer_slot(item_type),
        name=name.strip() or "未命名衣物",
        color=color.strip(),
        description=ai_description.strip(),
        features=features,
        image_filename=filename,
        relative_image_path=filename,
        image_status=ImageStatus.AVAILABLE,
        embedding_status=EmbeddingStatus.PENDING,
        raw_json_hash="",
        dataset_item_id="",
    )

    with database_session(database_path) as connection:
        register_dataset_source(
            connection,
            source=source,
            image_root=root,
            source_revision="personal-photo-v1",
        )
        upsert_items(connection, [item], "personal-photo-v1")
        if attributes:
            connection.execute(
                "UPDATE catalog_items SET attributes_json = %s WHERE item_id = %s",
                (json.dumps(attributes, ensure_ascii=False), item_id),
            )
        connection.execute(
            """
            INSERT INTO wardrobe_items(user_id, item_id, active, favorite, notes, added_at)
            VALUES (%s, %s, 1, 0, '', %s)
            ON CONFLICT(user_id, item_id) DO UPDATE SET active = 1
            """,
            (user_id, item_id, _now()),
        )
        connection.execute(
            """
            INSERT INTO personal_wardrobe_items(
                item_id, user_id, quantity_owned, ownership_status, review_status,
                created_at, updated_at
            ) VALUES (%s, %s, 1, 'owned', 'confirmed', %s, %s)
            ON CONFLICT(item_id) DO UPDATE SET
                ownership_status = 'owned', review_status = 'confirmed',
                updated_at = excluded.updated_at
            """,
            (item_id, user_id, _now(), _now()),
        )
        connection.execute(
            """
            INSERT INTO catalog_item_images(
                item_id, position, image_role, image_filename,
                relative_image_path, image_status, is_primary
            ) VALUES (%s, 0, 'primary', %s, %s, 'available', 1)
            ON CONFLICT(item_id, position) DO UPDATE SET
                image_role = excluded.image_role,
                image_filename = excluded.image_filename,
                relative_image_path = excluded.relative_image_path,
                image_status = excluded.image_status,
                is_primary = excluded.is_primary
            """,
            (item_id, filename, filename),
        )

    if skip_embedding:
        embedding = {"status": "skipped"}
    else:
        try:
            embedding = embed_personal_items(
                database_path=database_path,
                item_ids=[item_id],
                model_dir=model_dir,
                device=device,
                batch_size=1,
            )
        except Exception as error:  # noqa: BLE001 - item remains available for retry
            with database_session(database_path) as connection:
                mark_personal_embedding_failed(connection, [item_id])
            embedding = {
                "status": "failed",
                "error_type": type(error).__name__,
                "error_code": "PERSONAL_EMBEDDING_FAILED",
                "error": "衣物已保存，但向量生成失败，可稍后重试",
                "retryable": True,
            }

    with database_session(database_path) as connection:
        item_dict = _item_to_dict(connection, item_id)
    return {"item_id": item_id, "item": item_dict, "embedding": embedding}


def update_personal_item(
    *,
    database_path: str,
    user_id: str,
    item_id: str,
    model_dir: Path,
    device: str = "cuda",
    name: str | None = None,
    item_type: str | None = None,
    subtype: str | None = None,
    color: str | None = None,
    gender: str | None = None,
    size: str | None = None,
    attributes: dict | None = None,
) -> dict[str, Any]:
    """Update metadata of an item in the user's wardrobe."""
    initialize_database(database_path)
    with database_session(database_path) as connection:
        row = connection.execute(
            """
            SELECT c.* FROM catalog_items AS c
            JOIN wardrobe_items AS w ON w.item_id = c.item_id
            WHERE w.user_id = %s AND w.active = 1 AND c.item_id = %s
            """,
            (user_id, item_id),
        ).fetchone()
        if row is None:
            raise ValueError("Wardrobe item not found")

        updates: dict[str, str] = {}
        if name is not None:
            updates["name"] = name.strip()
        if item_type is not None:
            item_type = item_type.strip()
            if item_type not in ALLOWED_ITEM_TYPES:
                raise ValueError(f"Unsupported item_type: {item_type}")
            updates["item_type"] = item_type
            updates["main_category"] = infer_slot(item_type)
        if color is not None:
            updates["color"] = color.strip()
        if gender is not None:
            updates["gender"] = gender.strip()
        if attributes is not None:
            updates["attributes_json"] = json.dumps(attributes, ensure_ascii=False)
        if not updates:
            raise ValueError("No fields to update")

        # Rebuild features when subtype/size is provided.
        if subtype is not None or size is not None:
            existing = tuple(json.loads(row["features_json"]))
            features: list[str] = []
            new_subtype = subtype.strip() if subtype is not None else ""
            new_size = size.strip() if size is not None else ""
            for value in existing:
                if value.startswith("size:"):
                    if new_size:
                        features.append(f"size:{new_size}")
                elif new_subtype:
                    features.append(new_subtype)
                else:
                    features.append(value)
            if new_subtype and new_subtype not in features:
                features.insert(0, new_subtype)
            if new_size and f"size:{new_size}" not in features:
                features.append(f"size:{new_size}")
            updates["features_json"] = json.dumps(features, ensure_ascii=False)

        assignments = ", ".join(f"{column} = %s" for column in updates)
        connection.execute(
            f"UPDATE catalog_items SET {assignments}, embedding_status = 'pending' "  # noqa: S608
            f"WHERE item_id = %s",
            (*updates.values(), item_id),
        )
        invalidate_personal_embeddings(connection, [item_id])

    try:
        embedding = embed_personal_items(
            database_path=database_path,
            item_ids=[item_id],
            model_dir=model_dir,
            device=device,
            batch_size=1,
        )
    except Exception as error:  # noqa: BLE001 - metadata is durable; expose safe retry
        with database_session(database_path) as connection:
            mark_personal_embedding_failed(connection, [item_id])
        embedding = {
            "status": "failed",
            "error_type": type(error).__name__,
            "error_code": "PERSONAL_EMBEDDING_FAILED",
            "error": "衣物已更新，但向量生成失败，可稍后重试",
            "retryable": True,
        }

    with database_session(database_path) as connection:
        item_dict = _item_to_dict(connection, item_id)
    return {"item_id": item_id, "item": item_dict, "embedding": embedding}
