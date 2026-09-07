"""PostgreSQL persistence for resumable wardrobe photo-recognition batches."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Sequence

from styleforge.repositories.database import Connection


ACTIVE_BATCH_STATUSES = ("accepted", "recognizing", "embedding", "retrying")
TERMINAL_BATCH_STATUSES = ("completed", "partial_failed", "interrupted", "cancelled")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_batch(
    connection: Connection,
    *,
    batch_id: str,
    user_id: str,
    gender: str,
    auto_embed: bool,
    items: Sequence[dict[str, Any]],
) -> None:
    now = _now()
    connection.execute(
        """
        INSERT INTO recognition_batches(
            batch_id, user_id, status, total, gender, auto_embed,
            embedding_json, created_at, updated_at
        ) VALUES (%s, %s, 'accepted', %s, %s, %s, '{}', %s, %s)
        """,
        (batch_id, user_id, len(items), gender, int(auto_embed), now, now),
    )
    connection.executemany(
        """
        INSERT INTO recognition_batch_items(
            batch_id, item_index, filename, input_relative_path, input_sha256,
            status, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, 'pending', %s, %s)
        """,
        (
            (
                batch_id,
                int(item["index"]),
                str(item["filename"]),
                str(item["input_relative_path"]),
                str(item["input_sha256"]),
                now,
                now,
            )
            for item in items
        ),
    )


def _decode_batch(row: Any, items: Sequence[Any]) -> dict[str, Any]:
    embedding = json.loads(row["embedding_json"] or "{}")
    total = int(row["total"])
    done = int(row["done"])
    ema_ms = float(row["ema_ms"])
    eta_seconds = max(0, round(ema_ms * (total - done) / 3 / 1000))
    return {
        "batch_id": str(row["batch_id"]),
        "user_id": str(row["user_id"]),
        "total": total,
        "gender": str(row["gender"]),
        "auto_embed": bool(row["auto_embed"]),
        "done": done,
        "percent": round(done * 100 / total) if total else 100,
        "succeeded": int(row["succeeded"]),
        "failed": int(row["failed"]),
        "status": str(row["status"]),
        "embedding": embedding or None,
        "last_error_code": str(row["last_error_code"] or ""),
        "eta_seconds": eta_seconds,
        "started_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "finished_at": str(row["finished_at"]) if row["finished_at"] else None,
        "results": [_decode_item(item) for item in items],
    }


def _decode_item(row: Any) -> dict[str, Any]:
    return {
        "index": int(row["item_index"]),
        "filename": str(row["filename"]),
        "status": str(row["status"]),
        "reason": str(row["reason"] or ""),
        "item_id": str(row["wardrobe_item_id"] or ""),
        "item_type": str(row["item_type"] or ""),
        "subtype": str(row["subtype"] or ""),
        "color": str(row["color"] or ""),
        "name": str(row["name"] or ""),
        "confidence": float(row["confidence"]),
        "attributes": json.loads(row["attributes_json"] or "{}") or None,
        "attempt_count": int(row["attempt_count"]),
        "input_relative_path": str(row["input_relative_path"]),
    }


def get_batch(connection: Connection, batch_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM recognition_batches WHERE batch_id = %s",
        (batch_id,),
    ).fetchone()
    if row is None:
        return None
    items = connection.execute(
        "SELECT * FROM recognition_batch_items WHERE batch_id = %s ORDER BY item_index",
        (batch_id,),
    ).fetchall()
    return _decode_batch(row, items)


def list_batches(connection: Connection, user_id: str, limit: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT batch_id FROM recognition_batches WHERE user_id = %s "
        "ORDER BY updated_at DESC LIMIT %s",
        (user_id, max(1, min(int(limit), 100))),
    ).fetchall()
    return [
        batch
        for row in rows
        if (batch := get_batch(connection, str(row["batch_id"]))) is not None
    ]


def list_recoverable_batch_ids(connection: Connection) -> list[str]:
    rows = connection.execute(
        "SELECT batch_id FROM recognition_batches "
        "WHERE status = ANY(%s) ORDER BY updated_at",
        (list(ACTIVE_BATCH_STATUSES),),
    ).fetchall()
    return [str(row["batch_id"]) for row in rows]


def mark_batch_recovering(connection: Connection, batch_id: str) -> dict[str, Any] | None:
    now = _now()
    connection.execute(
        "UPDATE recognition_batch_items SET status = 'pending', updated_at = %s "
        "WHERE batch_id = %s AND status = 'recognizing'",
        (now, batch_id),
    )
    connection.execute(
        "UPDATE recognition_batches SET status = 'retrying', finished_at = NULL, "
        "last_error_code = '', updated_at = %s WHERE batch_id = %s",
        (now, batch_id),
    )
    return get_batch(connection, batch_id)


def claim_item(connection: Connection, batch_id: str, item_index: int) -> dict[str, Any] | None:
    row = connection.execute(
        """
        UPDATE recognition_batch_items
        SET status = 'recognizing', attempt_count = attempt_count + 1, updated_at = %s
        WHERE batch_id = %s AND item_index = %s AND status = 'pending'
        RETURNING *
        """,
        (_now(), batch_id, item_index),
    ).fetchone()
    if row is None:
        return None
    connection.execute(
        "UPDATE recognition_batches SET status = 'recognizing', updated_at = %s "
        "WHERE batch_id = %s AND status IN ('accepted', 'retrying')",
        (_now(), batch_id),
    )
    return _decode_item(row)


def finish_item(
    connection: Connection,
    *,
    batch_id: str,
    item_index: int,
    status: str,
    reason: str = "",
    wardrobe_item_id: str = "",
    item_type: str = "",
    subtype: str = "",
    color: str = "",
    name: str = "",
    confidence: float = 0.0,
    attributes: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        """
        UPDATE recognition_batch_items
        SET status = %s, reason = %s, wardrobe_item_id = NULLIF(%s, ''),
            item_type = %s, subtype = %s, color = %s, name = %s,
            confidence = %s, attributes_json = %s, updated_at = %s
        WHERE batch_id = %s AND item_index = %s
        """,
        (
            status,
            reason,
            wardrobe_item_id,
            item_type,
            subtype,
            color,
            name,
            max(0.0, min(float(confidence), 1.0)),
            json.dumps(attributes or {}, ensure_ascii=False),
            _now(),
            batch_id,
            item_index,
        ),
    )


def refresh_progress(
    connection: Connection,
    *,
    batch_id: str,
    wall_ms: float,
) -> tuple[dict[str, Any] | None, bool]:
    row = connection.execute(
        "SELECT * FROM recognition_batches WHERE batch_id = %s FOR UPDATE",
        (batch_id,),
    ).fetchone()
    if row is None:
        return None, False
    counts = connection.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE status IN ('succeeded', 'failed', 'cancelled')) AS done,
            COUNT(*) FILTER (WHERE status = 'succeeded') AS succeeded,
            COUNT(*) FILTER (WHERE status IN ('failed', 'cancelled')) AS failed,
            COUNT(*) FILTER (WHERE status IN ('pending', 'recognizing')) AS remaining
        FROM recognition_batch_items WHERE batch_id = %s
        """,
        (batch_id,),
    ).fetchone()
    done = int(counts["done"])
    succeeded = int(counts["succeeded"])
    failed = int(counts["failed"])
    remaining = int(counts["remaining"])
    prior_ema = float(row["ema_ms"])
    ema_ms = wall_ms if done <= 1 else prior_ema * 0.7 + wall_ms * 0.3
    status = str(row["status"])
    should_embed = False
    finished_at = None
    embedding_json = str(row["embedding_json"] or "{}")
    if remaining == 0:
        if bool(row["auto_embed"]) and succeeded > 0:
            existing = json.loads(embedding_json or "{}")
            embedding_status = str(existing.get("status") or "")
            if embedding_status == "completed":
                status = "partial_failed" if failed else "completed"
                finished_at = _now()
            elif embedding_status == "failed":
                # A late recognition callback must not turn an embedding failure
                # into an implicit retry. Retrying is an explicit API operation.
                status = "partial_failed"
                finished_at = row["finished_at"] or _now()
            elif embedding_status == "running" or status == "embedding":
                status = "embedding"
            else:
                should_embed = True
                status = "embedding"
                embedding_json = json.dumps(
                    {"status": "running", "requested_items": succeeded, "retryable": False}
                )
        else:
            status = "partial_failed" if failed else "completed"
            finished_at = _now()
            embedding_json = json.dumps(
                {
                    "status": "skipped",
                    "reason": "disabled" if not bool(row["auto_embed"]) else "no_recognized_items",
                    "requested_items": succeeded,
                    "retryable": False,
                }
            )
    connection.execute(
        """
        UPDATE recognition_batches
        SET status = %s, done = %s, succeeded = %s, failed = %s,
            ema_ms = %s, embedding_json = %s, updated_at = %s, finished_at = %s
        WHERE batch_id = %s
        """,
        (
            status,
            done,
            succeeded,
            failed,
            ema_ms,
            embedding_json,
            _now(),
            finished_at,
            batch_id,
        ),
    )
    return get_batch(connection, batch_id), should_embed


def finish_embedding(
    connection: Connection,
    *,
    batch_id: str,
    embedding: dict[str, Any],
) -> None:
    batch = connection.execute(
        "SELECT failed FROM recognition_batches WHERE batch_id = %s FOR UPDATE",
        (batch_id,),
    ).fetchone()
    if batch is None:
        return
    failed = int(batch["failed"])
    embedding_failed = embedding.get("status") == "failed"
    status = "partial_failed" if failed or embedding_failed else "completed"
    connection.execute(
        """
        UPDATE recognition_batches
        SET status = %s, embedding_json = %s, last_error_code = %s,
            updated_at = %s, finished_at = %s
        WHERE batch_id = %s
        """,
        (
            status,
            json.dumps(embedding, ensure_ascii=False),
            str(embedding.get("error_code") or ""),
            _now(),
            _now(),
            batch_id,
        ),
    )


def reset_failed_items(connection: Connection, batch_id: str) -> int:
    cursor = connection.execute(
        """
        UPDATE recognition_batch_items
        SET status = 'pending', reason = '', updated_at = %s
        WHERE batch_id = %s AND status = 'failed'
        """,
        (_now(), batch_id),
    )
    connection.execute(
        "UPDATE recognition_batches SET status = 'retrying', finished_at = NULL, "
        "last_error_code = '', updated_at = %s WHERE batch_id = %s",
        (_now(), batch_id),
    )
    return int(cursor.rowcount)


def fail_pending_items(connection: Connection, batch_id: str, reason: str) -> int:
    cursor = connection.execute(
        """
        UPDATE recognition_batch_items
        SET status = 'failed', reason = %s, updated_at = %s
        WHERE batch_id = %s AND status = 'pending'
        """,
        (reason, _now(), batch_id),
    )
    return int(cursor.rowcount)


def start_embedding_retry(connection: Connection, batch_id: str) -> bool:
    row = connection.execute(
        "SELECT embedding_json FROM recognition_batches "
        "WHERE batch_id = %s FOR UPDATE",
        (batch_id,),
    ).fetchone()
    if row is None:
        return False
    embedding = json.loads(row["embedding_json"] or "{}")
    if embedding.get("status") != "failed":
        return False
    connection.execute(
        """
        UPDATE recognition_batches
        SET status = 'embedding', embedding_json = %s, last_error_code = '',
            updated_at = %s, finished_at = NULL
        WHERE batch_id = %s
        """,
        (
            json.dumps(
                {
                    "status": "running",
                    "requested_items": int(embedding.get("requested_items") or 0),
                    "retryable": False,
                }
            ),
            _now(),
            batch_id,
        ),
    )
    return True


def mark_interrupted(connection: Connection, batch_id: str, error_code: str) -> None:
    connection.execute(
        "UPDATE recognition_batch_items SET status = 'pending', updated_at = %s "
        "WHERE batch_id = %s AND status = 'recognizing'",
        (_now(), batch_id),
    )
    connection.execute(
        "UPDATE recognition_batches SET status = 'interrupted', last_error_code = %s, "
        "updated_at = %s, finished_at = %s WHERE batch_id = %s",
        (error_code, _now(), _now(), batch_id),
    )


def delete_batch(connection: Connection, user_id: str, batch_id: str) -> bool:
    cursor = connection.execute(
        "DELETE FROM recognition_batches WHERE batch_id = %s AND user_id = %s "
        "AND status = ANY(%s)",
        (batch_id, user_id, list(TERMINAL_BATCH_STATUSES)),
    )
    return cursor.rowcount > 0
