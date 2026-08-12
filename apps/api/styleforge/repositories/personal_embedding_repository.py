"""SQLite-backed FashionCLIP embeddings for personal wardrobe items."""

from __future__ import annotations

from styleforge.repositories.database import Connection
from datetime import datetime, timezone
from typing import Sequence


def upsert_personal_embedding(
    connection: Connection,
    *,
    item_id: str,
    vector,
    embedding_kind: str,
    model_revision: str,
) -> None:
    import numpy as np

    normalized = np.asarray(vector, dtype=np.float32).reshape(-1)
    if normalized.size == 0 or not np.all(np.isfinite(normalized)):
        raise ValueError("Personal embedding must be a finite non-empty vector")
    norm = float(np.linalg.norm(normalized))
    if norm <= 1e-12:
        raise ValueError("Personal embedding must be non-zero")
    normalized = np.ascontiguousarray(normalized / norm, dtype=np.float32)
    connection.execute(
        """
        INSERT INTO personal_item_embeddings(
            item_id, embedding_kind, dimension, vector_blob, model_revision, embedded_at
        ) VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT(item_id) DO UPDATE SET
            embedding_kind = excluded.embedding_kind,
            dimension = excluded.dimension,
            vector_blob = excluded.vector_blob,
            model_revision = excluded.model_revision,
            embedded_at = excluded.embedded_at
        """,
        (
            item_id,
            embedding_kind,
            int(normalized.size),
            normalized.tobytes(),
            model_revision,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    connection.execute(
        "UPDATE catalog_items SET embedding_status = 'ready' WHERE item_id = %s",
        (item_id,),
    )


def mark_personal_embedding_failed(
    connection: Connection,
    item_ids: Sequence[str],
) -> None:
    connection.executemany(
        "UPDATE catalog_items SET embedding_status = 'failed' WHERE item_id = %s",
        ((item_id,) for item_id in item_ids),
    )


class PersonalEmbeddingStore:
    """Score normalized personal vectors without rebuilding the global matrix."""

    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def score_items(self, query_vector, allowed_item_ids: Sequence[str]) -> dict[str, float]:
        import numpy as np

        item_ids = tuple(dict.fromkeys(allowed_item_ids))
        if not item_ids:
            return {}
        query = np.asarray(query_vector, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(query))
        if norm <= 1e-12:
            raise ValueError("Query vector must be non-zero")
        query = query / norm
        placeholders = ",".join("%s" for _ in item_ids)
        rows = self.connection.execute(
            f"SELECT item_id, dimension, vector_blob FROM personal_item_embeddings "  # noqa: S608
            f"WHERE item_id IN ({placeholders})",
            item_ids,
        ).fetchall()
        scores = {}
        for row in rows:
            if int(row["dimension"]) != query.size:
                continue
            vector = np.frombuffer(row["vector_blob"], dtype=np.float32)
            if vector.shape != query.shape:
                continue
            scores[row["item_id"]] = float(vector @ query)
        return scores
