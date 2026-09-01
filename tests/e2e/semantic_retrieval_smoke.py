"""Real-artifact semantic wardrobe retrieval smoke test."""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
import uuid
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = WORKSPACE_ROOT / "apps" / "api"
MODEL_DIR = WORKSPACE_ROOT / "artifacts" / "models"
EMBEDDING_DIR = WORKSPACE_ROOT / "artifacts" / "embeddings" / "fashionclip"
ARTIFACT_ROOT = Path(
    os.getenv(
        "STYLEFORGE_E2E_ARTIFACT_ROOT",
        WORKSPACE_ROOT / "artifacts" / "e2e-runtime" / "semantic",
    )
).resolve()
DATABASE_DSN = os.getenv(
    "STYLEFORGE_E2E_DATABASE_DSN",
    "postgresql://styleforge@127.0.0.1:5432/styleforge_test",
)
PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAE0lEQVR4nGP88OED"
    "AwwwwVl4OQCNpALYc6NlUQAAAABJRU5ErkJggg=="
)
FIXTURES = (
    ("top", "白色通勤衬衫", "white"),
    ("pants", "蓝色休闲长裤", "blue"),
    ("shoes", "黑色通勤皮鞋", "black"),
)

sys.path.insert(0, str(API_ROOT))

from PIL import Image  # noqa: E402
import torch  # noqa: E402

from styleforge.repositories.database import database_session, initialize_database  # noqa: E402
from styleforge.repositories.personal_embedding_repository import (  # noqa: E402
    upsert_personal_embedding,
)
from styleforge.repositories.wardrobe_repository import list_items  # noqa: E402
from styleforge.services.personal_embeddings import DEFAULT_MODEL_REVISION  # noqa: E402
from styleforge.services.wardrobe_item_service import create_photo_item  # noqa: E402
from styleforge.services.wardrobe_retrieval import WardrobeHybridRetriever  # noqa: E402
from styleforge.vision.fashion_clip import FashionClipEncoder  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    started = time.perf_counter()
    require((MODEL_DIR / "config.json").is_file(), "FashionCLIP config is missing")
    require(
        (MODEL_DIR / "model.safetensors").is_file(),
        "FashionCLIP weights are missing",
    )
    require(
        (EMBEDDING_DIR / "manifest.json").is_file(),
        "Catalog embedding manifest is missing",
    )

    initialize_database(DATABASE_DSN)
    user_id = os.getenv("STYLEFORGE_SEMANTIC_E2E_USER") or (
        f"semantic-e2e-{uuid.uuid4().hex[:12]}"
    )
    image_bytes = base64.b64decode(PNG_BASE64)
    item_ids: list[str] = []
    for item_type, name, color in FIXTURES:
        created = create_photo_item(
            database_path=DATABASE_DSN,
            artifact_root=ARTIFACT_ROOT,
            user_id=user_id,
            image_bytes=image_bytes,
            model_dir=MODEL_DIR,
            item_type=item_type,
            name=name,
            color=color,
            attributes={"description": f"semantic E2E fixture: {name}"},
            skip_embedding=True,
        )
        item_ids.append(created["item_id"])

    device = "cuda" if torch.cuda.is_available() else "cpu"
    precision = "float16" if device == "cuda" else "float32"
    encoder = FashionClipEncoder(MODEL_DIR, device=device, precision=precision)
    with Image.open(io.BytesIO(image_bytes)) as source:
        images = [source.convert("RGB") for _ in item_ids]
    try:
        vectors = encoder.encode_images(images)
    finally:
        for image in images:
            image.close()

    with database_session(DATABASE_DSN) as connection:
        for item_id, vector in zip(item_ids, vectors, strict=True):
            upsert_personal_embedding(
                connection,
                item_id=item_id,
                vector=vector,
                embedding_kind="image",
                model_revision=DEFAULT_MODEL_REVISION,
            )
        wardrobe_items = list_items(connection, user_id)
        retriever = WardrobeHybridRetriever(
            embedding_dir=EMBEDDING_DIR,
            model_dir=MODEL_DIR,
            text_embedder=encoder,
        )
        semantic = retriever.search(
            "editorial monochrome silhouette",
            wardrobe_items,
            connection=connection,
            limit=3,
        )
        hybrid = retriever.search(
            "白色通勤衬衫",
            wardrobe_items,
            connection=connection,
            limit=3,
        )

        degraded = WardrobeHybridRetriever(
            embedding_dir=EMBEDDING_DIR,
            model_dir=ARTIFACT_ROOT / "missing-model",
        ).search(
            "白色通勤衬衫",
            wardrobe_items,
            connection=connection,
            limit=3,
        )

    allowed = set(item_ids)
    require(semantic.mode == "semantic", f"Expected semantic mode: {semantic}")
    require(semantic.semantic_available, f"Semantic retrieval unavailable: {semantic}")
    require(bool(semantic.item_ids), f"Semantic retrieval returned no items: {semantic}")
    require(set(semantic.item_ids) <= allowed, "Semantic retrieval escaped the wardrobe")
    require(hybrid.mode == "hybrid", f"Expected hybrid mode: {hybrid}")
    require(hybrid.semantic_available, f"Hybrid semantic scores missing: {hybrid}")
    require(set(hybrid.item_ids) <= allowed, "Hybrid retrieval escaped the wardrobe")
    require(degraded.mode == "keyword", f"Expected keyword fallback: {degraded}")
    require(not degraded.semantic_available, f"Fallback incorrectly marked semantic: {degraded}")
    require(bool(degraded.item_ids), f"Keyword fallback returned no items: {degraded}")

    print(
        json.dumps(
            {
                "status": "passed",
                "user_id": user_id,
                "device": device,
                "dimension": encoder.dimension,
                "embedded_items": len(item_ids),
                "semantic": {
                    "mode": semantic.mode,
                    "matched": semantic.matched,
                    "item_ids": semantic.item_ids,
                    "diagnostics": semantic.diagnostics,
                },
                "hybrid": {
                    "mode": hybrid.mode,
                    "matched": hybrid.matched,
                    "item_ids": hybrid.item_ids,
                    "diagnostics": hybrid.diagnostics,
                },
                "degraded": {
                    "mode": degraded.mode,
                    "matched": degraded.matched,
                    "item_ids": degraded.item_ids,
                    "diagnostics": degraded.diagnostics,
                },
                "duration_seconds": round(time.perf_counter() - started, 3),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
