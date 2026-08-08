"""Cross-request memory of request signatures and outfit structure signatures.

Keeps at most ``max_recent`` entries per user (application-level pruning),
mirroring the idempotent batch pattern used elsewhere in the project.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


def save_request_memory(
    connection: sqlite3.Connection,
    *,
    user_id: str,
    request_signature: dict[str, Any],
    structure_signature: dict[str, Any] | None = None,
    max_recent: int = 5,
) -> int:
    if not user_id.strip():
        raise ValueError("user_id cannot be empty")
    if not request_signature:
        return 0
    created_at = datetime.now(timezone.utc).isoformat()
    connection.execute(
        """
        INSERT INTO request_memory(
            user_id, request_signature_json, structure_signature_json, created_at
        ) VALUES (?, ?, ?, ?)
        """,
        (
            user_id,
            json.dumps(request_signature, ensure_ascii=False, sort_keys=True),
            json.dumps(
                structure_signature or {},
                ensure_ascii=False,
                sort_keys=True,
            ),
            created_at,
        ),
    )
    cursor = connection.execute(
        """
        DELETE FROM request_memory
        WHERE user_id = ?
          AND memory_id NOT IN (
              SELECT memory_id FROM request_memory
              WHERE user_id = ?
              ORDER BY created_at DESC, memory_id DESC
              LIMIT ?
          )
        """,
        (user_id, user_id, max_recent),
    )
    return cursor.rowcount


def _row_to_memory(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "memory_id": row["memory_id"],
        "user_id": row["user_id"],
        "request_signature": json.loads(row["request_signature_json"]),
        "structure_signature": json.loads(row["structure_signature_json"]),
        "created_at": row["created_at"],
    }


def recent_request_memories(
    connection: sqlite3.Connection,
    user_id: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT memory_id, user_id, request_signature_json, structure_signature_json, created_at
        FROM request_memory
        WHERE user_id = ?
        ORDER BY created_at DESC, memory_id DESC
        LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
    return [_row_to_memory(row) for row in rows]


def list_structure_signatures(
    connection: sqlite3.Connection,
    user_id: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT structure_signature_json
        FROM request_memory
        WHERE user_id = ? AND structure_signature_json <> '{}'
        ORDER BY created_at DESC, memory_id DESC
        LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
    return [json.loads(row["structure_signature_json"]) for row in rows]
