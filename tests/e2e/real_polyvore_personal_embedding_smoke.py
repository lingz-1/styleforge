"""Real Polyvore images -> personal PostgreSQL vectors -> wardrobe retrieval."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = WORKSPACE_ROOT / "apps" / "api"
MODEL_DIR = WORKSPACE_ROOT / "artifacts" / "models"
EMBEDDING_DIR = WORKSPACE_ROOT / "artifacts" / "embeddings" / "fashionclip"
ARTIFACT_ROOT = WORKSPACE_ROOT / "artifacts" / "e2e-runtime" / "real-personal-embedding"
FIXTURE_PATH = WORKSPACE_ROOT / "evals" / "fixtures" / "wardrobes.json"
DATASET_IMAGE_ROOT = Path(
    os.getenv(
        "STYLEFORGE_POLYVORE_IMAGE_ROOT",
        r"E:\01-style-dataset\p-outfit\images",
    )
).resolve()
DATABASE_DSN = os.getenv(
    "STYLEFORGE_E2E_DATABASE_DSN",
    "postgresql://styleforge@127.0.0.1:5432/styleforge_test",
)

sys.path.insert(0, str(API_ROOT))

import numpy as np  # noqa: E402
import torch  # noqa: E402

from styleforge.repositories.database import database_session, initialize_database  # noqa: E402
from styleforge.repositories.wardrobe_import_repository import (  # noqa: E402
    personal_image_root,
    personal_source_for_user,
)
from styleforge.repositories.wardrobe_repository import list_items  # noqa: E402
from styleforge.services.personal_embeddings import embed_personal_items  # noqa: E402
from styleforge.services.wardrobe_item_service import create_photo_item  # noqa: E402
from styleforge.services.wardrobe_retrieval import WardrobeHybridRetriever  # noqa: E402
from styleforge.vision.fashion_clip import shared_fashion_clip_encoder  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_real_items() -> list[dict]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    keys = set(payload["profiles"]["polyvore_one_piece"]["item_keys"])
    items = [item for item in payload["items"] if item["key"] in keys]
    require(len(items) == 11, f"Expected 11 real fixture items, got {len(items)}")
    for item in items:
        image_path = (DATASET_IMAGE_ROOT / item["relative_image_path"]).resolve()
        require(image_path.is_relative_to(DATASET_IMAGE_ROOT), "Image escaped dataset root")
        require(image_path.is_file(), f"Missing real image: {image_path}")
        item["resolved_image_path"] = image_path
    return items


def cleanup(user_id: str, item_ids: list[str]) -> None:
    with database_session(DATABASE_DSN) as connection:
        if not item_ids:
            item_ids = [
                str(row["item_id"])
                for row in connection.execute(
                    "SELECT item_id FROM personal_wardrobe_items WHERE user_id = %s",
                    (user_id,),
                ).fetchall()
            ]
        connection.execute("DELETE FROM wardrobe_items WHERE user_id = %s", (user_id,))
        if item_ids:
            connection.execute(
                "DELETE FROM catalog_items WHERE item_id = ANY(%s)",
                (item_ids,),
            )
        connection.execute(
            "DELETE FROM dataset_sources WHERE source = %s",
            (personal_source_for_user(user_id),),
        )
    image_root = personal_image_root(ARTIFACT_ROOT, user_id).resolve()
    safe_root = ARTIFACT_ROOT.resolve()
    if image_root.is_relative_to(safe_root) and image_root.is_dir():
        shutil.rmtree(image_root)


def cleanup_stale_runs() -> int:
    """Remove interrupted runs created only by this isolated smoke test."""
    with database_session(DATABASE_DSN) as connection:
        rows = connection.execute(
            "SELECT DISTINCT user_id FROM personal_wardrobe_items "
            "WHERE user_id LIKE 'polyvore-embedding-e2e-%'"
        ).fetchall()
    user_ids = [str(row["user_id"]) for row in rows]
    for user_id in user_ids:
        cleanup(user_id, [])
    return len(user_ids)


def main() -> None:
    started = time.perf_counter()
    require((MODEL_DIR / "config.json").is_file(), "FashionCLIP config is missing")
    require((MODEL_DIR / "model.safetensors").is_file(), "FashionCLIP weights are missing")
    require((EMBEDDING_DIR / "manifest.json").is_file(), "Catalog index is missing")
    items = load_real_items()
    initialize_database(DATABASE_DSN)
    stale_runs_removed = cleanup_stale_runs()

    user_id = f"polyvore-embedding-e2e-{uuid.uuid4().hex[:12]}"
    item_ids: list[str] = []
    names_by_id: dict[str, str] = {}
    source_keys_by_id: dict[str, str] = {}
    device = "cuda" if torch.cuda.is_available() else "cpu"
    precision = "float16" if device == "cuda" else "float32"

    try:
        for item in items:
            created = create_photo_item(
                database_path=DATABASE_DSN,
                artifact_root=ARTIFACT_ROOT,
                user_id=user_id,
                image_bytes=item["resolved_image_path"].read_bytes(),
                model_dir=MODEL_DIR,
                device=device,
                name=item["name"],
                item_type=item["item_type"],
                color=item["color"],
                attributes={
                    "description": item["description"],
                    "features": item.get("features") or [],
                    "source_dataset": "Polyvore Outfits",
                },
                skip_embedding=True,
            )
            item_id = created["item_id"]
            item_ids.append(item_id)
            names_by_id[item_id] = item["name"]
            source_keys_by_id[item_id] = item["key"]

        embedding = embed_personal_items(
            database_path=DATABASE_DSN,
            item_ids=item_ids,
            model_dir=MODEL_DIR,
            device=device,
            precision=precision,
            batch_size=len(item_ids),
        )
        require(embedding["status"] == "completed", f"Embedding failed: {embedding}")
        require(embedding["embedded_items"] == len(items), f"Missing vectors: {embedding}")
        require(embedding["image_embeddings"] == len(items), f"Image fallback: {embedding}")
        require(embedding["text_embeddings"] == 0, f"Unexpected text vectors: {embedding}")

        with database_session(DATABASE_DSN) as connection:
            rows = connection.execute(
                "SELECT p.item_id, p.embedding_kind, p.dimension, p.vector_blob, "
                "c.embedding_status FROM personal_item_embeddings AS p "
                "JOIN catalog_items AS c ON c.item_id = p.item_id "
                "WHERE p.item_id = ANY(%s) ORDER BY p.item_id",
                (item_ids,),
            ).fetchall()
            require(len(rows) == len(items), f"Stored vector count mismatch: {len(rows)}")
            dimensions = {int(row["dimension"]) for row in rows}
            kinds = {str(row["embedding_kind"]) for row in rows}
            statuses = {str(row["embedding_status"]) for row in rows}
            norms = [
                float(np.linalg.norm(np.frombuffer(row["vector_blob"], dtype=np.float32)))
                for row in rows
            ]
            require(dimensions == {512}, f"Unexpected dimensions: {dimensions}")
            require(kinds == {"image"}, f"Unexpected embedding kinds: {kinds}")
            require(statuses == {"ready"}, f"Unexpected item statuses: {statuses}")
            require(all(abs(norm - 1.0) < 1e-5 for norm in norms), f"Bad norms: {norms}")

            encoder = shared_fashion_clip_encoder(
                MODEL_DIR,
                device=device,
                precision=precision,
            )
            retriever = WardrobeHybridRetriever(
                embedding_dir=EMBEDDING_DIR,
                model_dir=MODEL_DIR,
                text_embedder=encoder,
            )
            wardrobe = list_items(connection, user_id)
            queries = {
                "green silk slip dress": "polyvore_151616863",
                "black pointed toe stiletto shoes": "polyvore_196144884",
                "orange luxury handbag": "polyvore_207483977",
                "黑色优雅花卉外套": "polyvore_95456824",
            }
            retrievals = {}
            for query, expected_key in queries.items():
                outcome = retriever.search(
                    query,
                    wardrobe,
                    connection=connection,
                    limit=3,
                )
                ranked_keys = [source_keys_by_id[item_id] for item_id in outcome.item_ids]
                require(outcome.semantic_available, f"No semantic scores for {query}: {outcome}")
                require(outcome.mode in {"semantic", "hybrid"}, f"Wrong mode: {outcome}")
                require(expected_key in ranked_keys, f"Expected {expected_key} for {query}: {ranked_keys}")
                retrievals[query] = {
                    "mode": outcome.mode,
                    "ranked_keys": ranked_keys,
                    "ranked_names": [names_by_id[item_id] for item_id in outcome.item_ids],
                    "semantic_scored": outcome.diagnostics.get("semantic_scored"),
                }

        print(
            json.dumps(
                {
                    "status": "passed",
                    "dataset": "Polyvore Outfits",
                    "real_images": len(items),
                    "device": device,
                    "embedding": embedding,
                    "stored": {
                        "table": "personal_item_embeddings",
                        "rows": len(rows),
                        "dimension": next(iter(dimensions)),
                        "kinds": sorted(kinds),
                        "statuses": sorted(statuses),
                        "norm_min": round(min(norms), 6),
                        "norm_max": round(max(norms), 6),
                    },
                    "retrievals": retrievals,
                    "duration_seconds": round(time.perf_counter() - started, 3),
                    "stale_runs_removed": stale_runs_removed,
                    "cleanup": "test rows and copied images removed",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        cleanup(user_id, item_ids)


if __name__ == "__main__":
    main()
