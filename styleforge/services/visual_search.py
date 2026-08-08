"""Read-only FashionCLIP vector search service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True, slots=True)
class SearchHit:
    item_id: str
    score: float


class FashionIndex:
    """Load a FAISS index and map vector results back to catalog item IDs."""

    def __init__(self, index_dir: Path) -> None:
        import faiss

        index_dir = index_dir.resolve()
        manifest_path = index_dir / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Index manifest not found: {manifest_path}")
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if self.manifest.get("status") != "completed":
            raise RuntimeError("FAISS index is not complete")
        self.item_ids: list[str] = json.loads(
            (index_dir / "item_ids.json").read_text(encoding="utf-8")
        )
        self.index = faiss.read_index(str(index_dir / "fashionclip.index"))
        if self.index.ntotal != len(self.item_ids):
            raise RuntimeError("FAISS row count does not match item ID mapping")

    @property
    def dimension(self) -> int:
        return int(self.index.d)

    def search(self, query_vectors, top_k: int = 20) -> list[list[SearchHit]]:
        """Search normalized vectors and return item IDs with cosine scores."""
        import numpy as np

        vectors = np.asarray(query_vectors, dtype=np.float32)
        if vectors.ndim == 1:
            vectors = vectors[None, :]
        if vectors.ndim != 2 or vectors.shape[1] != self.dimension:
            raise ValueError(
                f"Expected query shape (n, {self.dimension}), got {vectors.shape}"
            )
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        if np.any(norms <= 1e-12):
            raise ValueError("Query vectors must be non-zero")
        vectors = np.ascontiguousarray(vectors / norms, dtype=np.float32)
        scores, positions = self.index.search(vectors, min(top_k, len(self.item_ids)))
        return [
            [
                SearchHit(self.item_ids[int(position)], float(score))
                for position, score in zip(row_positions, row_scores)
                if position >= 0
            ]
            for row_positions, row_scores in zip(positions, scores)
        ]

    def search_texts(
        self,
        encoder,
        texts: Sequence[str],
        top_k: int = 20,
    ) -> list[list[SearchHit]]:
        """Encode text with FashionCLIP and search the shared vector space."""
        return self.search(encoder.encode_texts(texts), top_k=top_k)
