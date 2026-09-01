"""PostgreSQL-backed FashionCLIP embeddings for personal wardrobe items."""

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
    input_fingerprint: str = "",
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
            item_id, embedding_kind, dimension, vector_blob, model_revision,
            input_fingerprint, embedded_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT(item_id) DO UPDATE SET
            embedding_kind = excluded.embedding_kind,
            dimension = excluded.dimension,
            vector_blob = excluded.vector_blob,
            model_revision = excluded.model_revision,
            input_fingerprint = excluded.input_fingerprint,
            embedded_at = excluded.embedded_at
        """,
        (
            item_id,
            embedding_kind,
            int(normalized.size),
            normalized.tobytes(),
            model_revision,
            input_fingerprint,
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


def invalidate_personal_embeddings(
    connection: Connection,
    item_ids: Sequence[str],
) -> None:
    """Atomically hide and remove vectors whose source input has changed."""
    ids = tuple(dict.fromkeys(str(item_id) for item_id in item_ids if item_id))
    if not ids:
        return
    connection.execute(
        "DELETE FROM personal_item_embeddings WHERE item_id = ANY(%s)",
        (list(ids),),
    )
    connection.execute(
        "UPDATE catalog_items SET embedding_status = 'pending' "
        "WHERE item_id = ANY(%s)",
        (list(ids),),
    )


def list_retryable_personal_item_ids(
    connection: Connection,
    *,
    user_id: str,
    requested_item_ids: Sequence[str] = (),
    limit: int = 200,
) -> list[str]:
    """Return this user's confirmed pending/failed items, never another user's."""
    cap = max(1, min(int(limit), 200))
    item_ids = tuple(dict.fromkeys(str(item_id) for item_id in requested_item_ids if item_id))
    params: list[object] = [user_id]
    requested_filter = ""
    if item_ids:
        requested_filter = "AND p.item_id = ANY(%s)"
        params.append(list(item_ids))
    params.append(cap)
    rows = connection.execute(
        f"""
        SELECT p.item_id
        FROM personal_wardrobe_items AS p
        JOIN catalog_items AS c ON c.item_id = p.item_id
        WHERE p.user_id = %s
          AND p.ownership_status = 'owned'
          AND p.review_status = 'confirmed'
          AND c.embedding_status IN ('pending', 'failed')
          {requested_filter}
        ORDER BY p.updated_at, p.item_id
        LIMIT %s
        """,  # noqa: S608 - only the fixed optional clause is interpolated
        params,
    ).fetchall()
    return [str(row["item_id"]) for row in rows]


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
            f"SELECT p.item_id, p.dimension, p.vector_blob "  # noqa: S608
            f"FROM personal_item_embeddings AS p "
            f"JOIN catalog_items AS c ON c.item_id = p.item_id "
            f"WHERE p.item_id IN ({placeholders}) AND c.embedding_status = 'ready'",
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
