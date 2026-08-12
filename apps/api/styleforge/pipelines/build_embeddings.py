"""Build resumable FashionCLIP embeddings for the local catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

from PIL import Image

from styleforge.common.files import write_json_atomic
from styleforge.core.config import Settings
from styleforge.repositories.database import connect, initialize_database
from styleforge.vision.fashion_clip import FashionClipEncoder


SCHEMA_VERSION = 2
DEFAULT_MODEL_REVISION = "7e3ba62ce16b379a1ab479346b66f192e76f51b7"
ARTIFACT_NAMES = (
    "embeddings.npy",
    "item_ids.json",
    "failures.json",
    "manifest.json",
    "state.json",
)


class CatalogImage(NamedTuple):
    item_id: str
    source: str
    relative_path: str
    absolute_path: Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _catalog_hash(rows: list[CatalogImage]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(row.item_id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(row.source.encode("utf-8"))
        digest.update(b"\0")
        digest.update(row.relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(row.absolute_path).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _load_catalog(
    database_path: str,
    fallback_image_root: Path | None,
    source: str | None,
    limit: int | None,
) -> tuple[list[CatalogImage], dict[str, str]]:
    connection = connect(database_path)
    try:
        sql = (
            "SELECT c.item_id, c.source, c.relative_image_path, d.image_root "
            "FROM catalog_items c "
            "LEFT JOIN dataset_sources d ON d.source = c.source "
            "WHERE c.image_status = 'available'"
        )
        parameters: list[object] = []
        if source is not None:
            sql += " AND c.source = %s"
            parameters.append(source)
        else:
            sql += " AND c.source NOT ILIKE 'personal-%'"
        sql += " ORDER BY c.item_id"
        if limit is not None:
            if limit <= 0:
                raise ValueError("limit must be positive")
            sql += " LIMIT %s"
            parameters.append(limit)
        rows = []
        roots: dict[str, str] = {}
        missing_sources: set[str] = set()
        for row in connection.execute(sql, parameters):
            root_value = row["image_root"]
            if not root_value and row["source"] == "polyvore" and fallback_image_root:
                root_value = str(fallback_image_root)
            if not root_value:
                missing_sources.add(row["source"])
                continue
            image_root = Path(root_value).resolve()
            image_path = (image_root / row["relative_image_path"]).resolve()
            if not image_path.is_relative_to(image_root):
                raise RuntimeError(f"Invalid image path for item {row['item_id']}")
            roots[row["source"]] = str(image_root)
            rows.append(
                CatalogImage(
                    row["item_id"],
                    row["source"],
                    row["relative_image_path"],
                    image_path,
                )
            )
        if missing_sources:
            joined = ", ".join(sorted(missing_sources))
            raise RuntimeError(f"Image roots are not configured for sources: {joined}")
        return rows, roots
    finally:
        connection.close()


def _load_rgb(path: Path) -> Image.Image:
    with Image.open(path) as image:
        return image.convert("RGB")


def _load_batch(paths: list[Path], workers: int) -> list[Image.Image]:
    if workers <= 1:
        return [_load_rgb(path) for path in paths]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(_load_rgb, paths))


def _validate_resume_state(state: dict[str, Any], signature: dict[str, Any]) -> int:
    mismatches = {
        key: (state.get(key), expected)
        for key, expected in signature.items()
        if state.get(key) != expected
    }
    if mismatches:
        details = ", ".join(
            f"{key}: saved={saved!r}, current={current!r}"
            for key, (saved, current) in mismatches.items()
        )
        raise RuntimeError(
            "Existing embedding state does not match this run. "
            f"Use a different output directory or --force. Differences: {details}"
        )
    next_index = int(state.get("next_index", 0))
    if not 0 <= next_index <= int(signature["total_items"]):
        raise RuntimeError(f"Invalid resume position: {next_index}")
    return next_index


def _mark_database_ready(database_path: str, item_ids: list[str]) -> None:
    connection = connect(database_path)
    try:
        connection.executemany(
            "UPDATE catalog_items SET embedding_status = 'ready' WHERE item_id = %s",
            ((item_id,) for item_id in item_ids),
        )
        connection.commit()
    finally:
        connection.close()


def build_embeddings(
    *,
    database_path: str,
    image_root: Path | None,
    model_dir: Path,
    output_dir: Path,
    batch_size: int,
    workers: int,
    device: str,
    precision: str,
    model_revision: str,
    source: str | None = None,
    limit: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Create a resumable, normalized FashionCLIP embedding matrix."""
    import numpy as np

    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if workers < 0:
        raise ValueError("workers must be non-negative")
    image_root = image_root.resolve() if image_root is not None else None
    model_dir = model_dir.resolve()
    output_dir = output_dir.resolve()
    initialize_database(database_path)
    if image_root is not None and not image_root.is_dir():
        raise FileNotFoundError(f"Image root not found: {image_root}")

    rows, image_roots = _load_catalog(database_path, image_root, source, limit)
    if not rows:
        raise RuntimeError("No available catalog images were found")
    for source_name, root_value in image_roots.items():
        source_root = Path(root_value).resolve()
        if output_dir == source_root or output_dir.is_relative_to(source_root):
            raise ValueError(
                f"Output directory must not be inside the {source_name} image root"
            )
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {name: output_dir / name for name in ARTIFACT_NAMES}
    if force:
        for path in paths.values():
            path.unlink(missing_ok=True)

    item_ids = [row.item_id for row in rows]
    model_weights = model_dir / "model.safetensors"
    if not model_weights.is_file():
        raise FileNotFoundError(f"Model weights not found: {model_weights}")

    print("Loading FashionCLIP and computing run fingerprints...", flush=True)
    model_sha256 = _sha256_file(model_weights)
    encoder = FashionClipEncoder(model_dir, device=device, precision=precision)
    signature = {
        "schema_version": SCHEMA_VERSION,
        "catalog_sha256": _catalog_hash(rows),
        "model_sha256": model_sha256,
        "model_revision": model_revision,
        "total_items": len(rows),
        "dimension": encoder.dimension,
        "dtype": "float32",
        "normalized": True,
        "precision": precision,
        "source_filter": source,
    }

    if paths["manifest.json"].is_file():
        manifest = json.loads(paths["manifest.json"].read_text(encoding="utf-8"))
        _validate_resume_state(manifest, signature)
        if manifest.get("status") == "completed":
            if limit is None:
                _mark_database_ready(database_path, item_ids)
            print("Embedding artifact is already complete.", flush=True)
            return manifest

    if paths["state.json"].is_file():
        state = json.loads(paths["state.json"].read_text(encoding="utf-8"))
        next_index = _validate_resume_state(state, signature)
        embeddings = np.load(paths["embeddings.npy"], mmap_mode="r+")
        if embeddings.shape != (len(rows), encoder.dimension) or embeddings.dtype != np.float32:
            raise RuntimeError("Existing embeddings.npy shape or dtype does not match state.json")
        started_at = str(state.get("started_at", _now()))
        print(f"Resuming at item {next_index}/{len(rows)}.", flush=True)
    else:
        next_index = 0
        started_at = _now()
        embeddings = np.lib.format.open_memmap(
            paths["embeddings.npy"],
            mode="w+",
            dtype=np.float32,
            shape=(len(rows), encoder.dimension),
        )
        write_json_atomic(paths["item_ids.json"], item_ids)
        state = {
            **signature,
            "status": "running",
            "next_index": 0,
            "batch_size": batch_size,
            "image_roots": image_roots,
            "model_dir": str(model_dir),
            "database_path": str(database_path),
            "started_at": started_at,
            "updated_at": started_at,
        }
        write_json_atomic(paths["state.json"], state)

    run_started = time.perf_counter()
    completed_at_start = next_index
    while next_index < len(rows):
        end_index = min(next_index + batch_size, len(rows))
        batch = rows[next_index:end_index]
        image_paths = [row.absolute_path for row in batch]
        try:
            images = _load_batch(image_paths, workers)
            vectors = encoder.encode_images(images)
        except BaseException as error:
            failure = {
                "failed_at": _now(),
                "start_index": next_index,
                "end_index": end_index,
                "item_ids": [row.item_id for row in batch],
                "image_paths": [str(path) for path in image_paths],
                "error_type": type(error).__name__,
                "error": str(error),
            }
            write_json_atomic(paths["failures.json"], failure)
            raise
        finally:
            if "images" in locals():
                for image in images:
                    image.close()
                del images

        if vectors.shape != (len(batch), encoder.dimension):
            raise RuntimeError(
                f"Unexpected embedding shape {vectors.shape}; "
                f"expected {(len(batch), encoder.dimension)}"
            )
        embeddings[next_index:end_index] = vectors
        embeddings.flush()
        next_index = end_index
        elapsed = max(time.perf_counter() - run_started, 1e-9)
        processed_this_run = next_index - completed_at_start
        rate = processed_this_run / elapsed
        state = {
            **signature,
            "status": "running",
            "next_index": next_index,
            "batch_size": batch_size,
            "image_roots": image_roots,
            "model_dir": str(model_dir),
            "database_path": str(database_path),
            "started_at": started_at,
            "updated_at": _now(),
            "items_per_second": round(rate, 3),
        }
        write_json_atomic(paths["state.json"], state)
        print(
            json.dumps(
                {
                    "processed": next_index,
                    "total": len(rows),
                    "percent": round(next_index * 100 / len(rows), 2),
                    "items_per_second": round(rate, 2),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    total_elapsed = max(time.perf_counter() - run_started, 1e-9)
    final_state = {
        **state,
        "status": "completed",
        "next_index": len(rows),
        "completed_at": _now(),
    }
    manifest = {
        **signature,
        "status": "completed",
        "embedding_version": f"fashionclip-{model_revision[:8]}-l2-v1",
        "embeddings_path": str(paths["embeddings.npy"]),
        "item_ids_path": str(paths["item_ids.json"]),
        "image_roots": image_roots,
        "model_dir": str(model_dir),
        "database_path": str(database_path),
        "batch_size": batch_size,
        "workers": workers,
        "device": device,
        "started_at": started_at,
        "completed_at": final_state["completed_at"],
        "last_run_seconds": round(total_elapsed, 3),
    }
    write_json_atomic(paths["state.json"], final_state)
    write_json_atomic(paths["manifest.json"], manifest)
    paths["failures.json"].unlink(missing_ok=True)
    if limit is None:
        _mark_database_ready(database_path, item_ids)
    return manifest


def _build_parser() -> argparse.ArgumentParser:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=str, default=settings.database_dsn)
    parser.add_argument(
        "--image-root",
        type=Path,
        default=settings.image_root,
        help="Fallback image root for an unregistered Polyvore source.",
    )
    parser.add_argument("--source", help="Optional catalog source filter.")
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=settings.artifact_root / "models",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=settings.embedding_dir,
    )
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    parser.add_argument("--device", default="cuda")
    parser.add_argument(
        "--precision",
        choices=("float32", "float16", "bfloat16"),
        default="float16",
    )
    parser.add_argument("--model-revision", default=DEFAULT_MODEL_REVISION)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force", action="store_true")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    manifest = build_embeddings(
        database_path=args.database,
        image_root=args.image_root,
        model_dir=args.model_dir,
        output_dir=args.output_dir,
        batch_size=args.batch_size,
        workers=args.workers,
        device=args.device,
        precision=args.precision,
        model_revision=args.model_revision,
        source=args.source,
        limit=args.limit,
        force=args.force,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
