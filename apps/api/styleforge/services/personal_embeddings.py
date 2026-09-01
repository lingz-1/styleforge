"""Incremental FashionCLIP embedding jobs for confirmed personal items."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.personal_embedding_repository import (
    mark_personal_embedding_failed,
    upsert_personal_embedding,
)
from styleforge.vision.fashion_clip import (
    SharedFashionClipEncoder,
    clear_shared_fashion_clip_runtime_cache,
    shared_fashion_clip_encoder,
)


TYPE_PROMPTS = {
    "top": "top",
    "pants": "pants",
    "shorts": "shorts",
    "skirt": "skirt",
    "dress": "dress",
    "jumpsuit": "jumpsuit",
    "suit": "suit",
    "outfit_set": "matching outfit set",
    "outwear": "outerwear",
    "shoes": "shoes",
    "bag": "bag",
    "eyewear": "eyewear",
    "earrings": "earrings",
    "necklace": "necklace",
    "bracelet": "bracelet",
    "rings": "ring",
    "belts": "belt",
    "hats": "hat",
    "hairwear": "hair accessory",
    "jewellery": "jewellery",
    "legwear": "legwear",
    "underwear": "underwear",
    "sleepwear": "sleepwear",
}

DEFAULT_MODEL_REVISION = "7e3ba62ce16b379a1ab479346b66f192e76f51b7"


def clear_personal_embedding_runtime_cache() -> None:
    """Test/maintenance hook; normal requests reuse the process-local runtime."""
    clear_shared_fashion_clip_runtime_cache()


def _personal_rows(database_path: str, item_ids: Sequence[str]):
    ids = tuple(dict.fromkeys(item_ids))
    if not ids:
        return []
    placeholders = ",".join("%s" for _ in ids)
    with database_session(database_path) as connection:
        return connection.execute(
            f"""
            SELECT c.item_id, c.source, c.gender, c.item_type, c.name, c.color,
                   c.description, c.relative_image_path, c.image_status, d.image_root
            FROM catalog_items AS c
            JOIN personal_wardrobe_items AS p ON p.item_id = c.item_id
            LEFT JOIN dataset_sources AS d ON d.source = c.source
            WHERE c.item_id IN ({placeholders})
              AND p.ownership_status = 'owned'
              AND p.review_status = 'confirmed'
            ORDER BY c.item_id
            """,  # noqa: S608
            ids,
        ).fetchall()


def _image_path(row) -> Path | None:
    if row["image_status"] != "available" or not row["relative_image_path"]:
        return None
    if not row["image_root"]:
        return None
    root = Path(row["image_root"]).resolve()
    relative = PurePosixPath(row["relative_image_path"])
    resolved = root.joinpath(*relative.parts).resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        return None
    return resolved


def _text_prompt(row) -> str:
    garment_type = TYPE_PROMPTS.get(row["item_type"], row["item_type"])
    audience = row["gender"] or "person"
    details = ", ".join(
        value for value in (row["color"], row["description"], row["name"]) if value
    )
    return f"A {audience} {garment_type}. {details}"[:1000]


def _input_fingerprint(*, kind: str, payload: bytes, model_revision: str) -> str:
    digest = hashlib.sha256()
    digest.update(model_revision.encode("utf-8"))
    digest.update(b"\0")
    digest.update(kind.encode("utf-8"))
    digest.update(b"\0")
    digest.update(payload)
    return digest.hexdigest()


def _encode_rows(
    encoder: SharedFashionClipEncoder,
    rows: Sequence[Any],
    *,
    batch_size: int,
    model_revision: str,
) -> list[tuple[str, object, str, str]]:
    encoded: list[tuple[str, object, str, str]] = []
    text_rows = []
    image_rows = []
    fallback_item_ids: set[str] = set()
    for row in rows:
        path = _image_path(row)
        if path is None:
            text_rows.append(row)
        else:
            image_rows.append((row, path))

    for start in range(0, len(image_rows), batch_size):
        from PIL import Image

        batch = image_rows[start : start + batch_size]
        opened = []
        valid_rows = []
        fingerprints = []
        try:
            for row, path in batch:
                try:
                    payload = path.read_bytes()
                    with Image.open(path) as image:
                        opened.append(image.convert("RGB"))
                    valid_rows.append(row)
                    fingerprints.append(
                        _input_fingerprint(
                            kind="image",
                            payload=payload,
                            model_revision=model_revision,
                        )
                    )
                except OSError:
                    text_rows.append(row)
                    fallback_item_ids.add(str(row["item_id"]))
            if opened:
                vectors = encoder.encode_images(opened)
                encoded.extend(
                    (str(row["item_id"]), vector, "image", fingerprint)
                    for row, vector, fingerprint in zip(
                        valid_rows, vectors, fingerprints, strict=True
                    )
                )
        finally:
            for image in opened:
                image.close()

    for start in range(0, len(text_rows), batch_size):
        batch = text_rows[start : start + batch_size]
        prompts = [_text_prompt(row) for row in batch]
        vectors = encoder.encode_texts(prompts)
        for row, prompt, vector in zip(batch, prompts, vectors, strict=True):
            kind = (
                "text_fallback"
                if str(row["item_id"]) in fallback_item_ids
                else "text"
            )
            encoded.append(
                (
                    str(row["item_id"]),
                    vector,
                    kind,
                    _input_fingerprint(
                        kind=kind,
                        payload=prompt.encode("utf-8"),
                        model_revision=model_revision,
                    ),
                )
            )
    return encoded


def embed_personal_items(
    *,
    database_path: str,
    item_ids: Sequence[str],
    model_dir: Path,
    device: str = "cuda",
    precision: str = "float16",
    model_revision: str = DEFAULT_MODEL_REVISION,
    batch_size: int = 32,
) -> dict[str, Any]:
    """Embed only selected personal items, using text when no image is available."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    initialize_database(database_path)
    rows = _personal_rows(database_path, item_ids)
    if not rows:
        return {
            "status": "completed",
            "requested_items": len(tuple(dict.fromkeys(item_ids))),
            "embedded_items": 0,
            "image_embeddings": 0,
            "text_embeddings": 0,
            "missing_or_unconfirmed_items": len(tuple(dict.fromkeys(item_ids))),
        }

    try:
        runtime = shared_fashion_clip_encoder(
            model_dir,
            device=device,
            precision=precision,
        )
    except Exception:
        with database_session(database_path) as connection:
            mark_personal_embedding_failed(
                connection, [str(row["item_id"]) for row in rows]
            )
        raise

    try:
        with runtime.inference_lock:
            encoded = _encode_rows(
                runtime,
                rows,
                batch_size=batch_size,
                model_revision=model_revision,
            )
    except Exception:
        with database_session(database_path) as connection:
            mark_personal_embedding_failed(
                connection, [str(row["item_id"]) for row in rows]
            )
        raise

    with database_session(database_path) as connection:
        for item_id, vector, kind, fingerprint in encoded:
            upsert_personal_embedding(
                connection,
                item_id=item_id,
                vector=vector,
                embedding_kind=kind,
                model_revision=model_revision,
                input_fingerprint=fingerprint,
            )
    return {
        "status": "completed",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_revision": model_revision,
        "dimension": runtime.dimension,
        "requested_items": len(tuple(dict.fromkeys(item_ids))),
        "embedded_items": len(encoded),
        "image_embeddings": sum(kind == "image" for _, _, kind, _ in encoded),
        "text_embeddings": sum(kind != "image" for _, _, kind, _ in encoded),
        "missing_or_unconfirmed_items": len(tuple(dict.fromkeys(item_ids))) - len(rows),
    }
