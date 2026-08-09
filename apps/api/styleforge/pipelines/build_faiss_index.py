"""Build and validate an exact cosine-similarity FAISS index."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from styleforge.common.files import write_json_atomic
from styleforge.core.config import Settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_faiss_index(
    *,
    embedding_dir: Path,
    output_dir: Path,
    add_batch_size: int = 8192,
    force: bool = False,
) -> dict[str, Any]:
    """Build IndexFlatIP over normalized embeddings and verify self-retrieval."""
    import faiss
    import numpy as np

    if add_batch_size <= 0:
        raise ValueError("add_batch_size must be positive")
    embedding_dir = embedding_dir.resolve()
    output_dir = output_dir.resolve()
    source_manifest_path = embedding_dir / "manifest.json"
    embeddings_path = embedding_dir / "embeddings.npy"
    item_ids_path = embedding_dir / "item_ids.json"
    for path in (source_manifest_path, embeddings_path, item_ids_path):
        if not path.is_file():
            raise FileNotFoundError(f"Required embedding artifact is missing: {path}")

    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if source_manifest.get("status") != "completed":
        raise RuntimeError("Embedding manifest is not completed")
    embeddings = np.load(embeddings_path, mmap_mode="r")
    item_ids = json.loads(item_ids_path.read_text(encoding="utf-8"))
    if embeddings.ndim != 2:
        raise RuntimeError(f"Expected a 2D embedding matrix, got {embeddings.shape}")
    if len(item_ids) != embeddings.shape[0]:
        raise RuntimeError("item_ids.json length does not match embeddings.npy")
    if embeddings.shape[1] != int(source_manifest["dimension"]):
        raise RuntimeError("Embedding dimension does not match manifest")

    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = output_dir / "fashionclip.index"
    index_item_ids_path = output_dir / "item_ids.json"
    manifest_path = output_dir / "manifest.json"
    if manifest_path.is_file() and not force:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("status") == "completed"
            and manifest.get("catalog_sha256") == source_manifest.get("catalog_sha256")
            and manifest.get("model_sha256") == source_manifest.get("model_sha256")
            and index_path.is_file()
        ):
            print("FAISS index is already complete.", flush=True)
            return manifest

    index = faiss.IndexFlatIP(embeddings.shape[1])
    for start in range(0, embeddings.shape[0], add_batch_size):
        end = min(start + add_batch_size, embeddings.shape[0])
        vectors = np.ascontiguousarray(embeddings[start:end], dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1)
        if not np.all(np.isfinite(vectors)):
            raise RuntimeError(f"Non-finite embedding detected in rows {start}:{end}")
        if not np.allclose(norms, 1.0, atol=2e-3):
            raise RuntimeError(f"Non-normalized embedding detected in rows {start}:{end}")
        index.add(vectors)
        print(
            json.dumps({"indexed": end, "total": embeddings.shape[0]}, ensure_ascii=False),
            flush=True,
        )

    probe_indices = sorted({0, embeddings.shape[0] // 2, embeddings.shape[0] - 1})
    probes = np.ascontiguousarray(embeddings[probe_indices], dtype=np.float32)
    validation_k = min(20, embeddings.shape[0])
    scores, neighbors = index.search(probes, validation_k)
    self_retrieval = [
        {
            "probe_index": expected,
            "retrieved_index": int(row_neighbors[0]),
            "score": float(row_scores[0]),
            "exact_item_rank": next(
                (
                    rank
                    for rank, position in enumerate(row_neighbors, start=1)
                    if int(position) == expected
                ),
                None,
            ),
            "mapping_roundtrip_passed": bool(
                np.allclose(index.reconstruct(expected), probes[probe_number], atol=1e-6)
            ),
            "passed": bool(
                float(row_scores[0]) >= 0.999
                and np.allclose(index.reconstruct(expected), probes[probe_number], atol=1e-6)
            ),
        }
        for probe_number, (expected, row_neighbors, row_scores) in enumerate(
            zip(probe_indices, neighbors, scores)
        )
    ]
    if not all(result["passed"] for result in self_retrieval):
        raise RuntimeError(f"FAISS self-retrieval validation failed: {self_retrieval}")

    temp_handle = tempfile.NamedTemporaryFile(
        dir=output_dir,
        prefix=".fashionclip.",
        suffix=".index.tmp",
        delete=False,
    )
    temp_index_path = Path(temp_handle.name)
    temp_handle.close()
    try:
        faiss.write_index(index, str(temp_index_path))
        os.replace(temp_index_path, index_path)
    except BaseException:
        temp_index_path.unlink(missing_ok=True)
        raise
    write_json_atomic(index_item_ids_path, item_ids)
    manifest = {
        "schema_version": 1,
        "status": "completed",
        "index_type": "IndexFlatIP",
        "metric": "cosine_via_normalized_inner_product",
        "total_items": int(index.ntotal),
        "dimension": int(embeddings.shape[1]),
        "catalog_sha256": source_manifest["catalog_sha256"],
        "model_sha256": source_manifest["model_sha256"],
        "embedding_version": source_manifest["embedding_version"],
        "index_path": str(index_path),
        "item_ids_path": str(index_item_ids_path),
        "source_embedding_manifest": str(source_manifest_path),
        "self_retrieval": self_retrieval,
        "completed_at": _now(),
    }
    write_json_atomic(manifest_path, manifest)
    return manifest


def _build_parser() -> argparse.ArgumentParser:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--embedding-dir",
        type=Path,
        default=settings.embedding_dir,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=settings.index_dir,
    )
    parser.add_argument("--add-batch-size", type=int, default=8192)
    parser.add_argument("--force", action="store_true")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    manifest = build_faiss_index(
        embedding_dir=args.embedding_dir,
        output_dir=args.output_dir,
        add_batch_size=args.add_batch_size,
        force=args.force,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
