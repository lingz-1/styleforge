"""Restricted FashionCLIP scoring over a user's own wardrobe items."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence


class CatalogVectorStore:
    """Memory-map catalog vectors and score only explicitly allowed item IDs."""

    def __init__(self, embedding_dir: Path) -> None:
        import numpy as np

        embedding_dir = embedding_dir.resolve()
        manifest_path = embedding_dir / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Embedding manifest not found: {manifest_path}")
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if self.manifest.get("status") != "completed":
            raise RuntimeError("Embedding artifact is not complete")
        self.item_ids: list[str] = json.loads(
            (embedding_dir / "item_ids.json").read_text(encoding="utf-8")
        )
        self.embeddings = np.load(embedding_dir / "embeddings.npy", mmap_mode="r")
        if len(self.item_ids) != self.embeddings.shape[0]:
            raise RuntimeError("Embedding rows do not match the item ID mapping")
        self.position_by_id = {
            item_id: position for position, item_id in enumerate(self.item_ids)
        }

    @property
    def dimension(self) -> int:
        return int(self.embeddings.shape[1])

    def score_items(
        self,
        query_vector,
        allowed_item_ids: Sequence[str],
    ) -> dict[str, float]:
        """Return raw cosine scores without leaking items outside the allow-list."""
        import numpy as np

        vector = np.asarray(query_vector, dtype=np.float32).reshape(-1)
        if vector.shape != (self.dimension,):
            raise ValueError(
                f"Expected query dimension {self.dimension}, got {vector.shape}"
            )
        norm = float(np.linalg.norm(vector))
        if norm <= 1e-12:
            raise ValueError("Query vector must be non-zero")
        vector = vector / norm
        pairs = [
            (item_id, self.position_by_id[item_id])
            for item_id in dict.fromkeys(allowed_item_ids)
            if item_id in self.position_by_id
        ]
        if not pairs:
            return {}
        matrix = np.asarray(
            self.embeddings[[position for _, position in pairs]],
            dtype=np.float32,
        )
        scores = matrix @ vector
        return {
            item_id: float(score)
            for (item_id, _), score in zip(pairs, scores, strict=True)
        }


def normalize_relevance(scores: dict[str, float]) -> dict[str, float]:
    """Scale a slot-local score distribution to an interpretable 0-100 range."""
    if not scores:
        return {}
    low = min(scores.values())
    high = max(scores.values())
    if high - low <= 1e-9:
        return {item_id: 50.0 for item_id in scores}
    return {
        item_id: 100.0 * (score - low) / (high - low)
        for item_id, score in scores.items()
    }
