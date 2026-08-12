"""Long-term user preference memories.

An ``auto`` memory is distilled from a user request by the LLM extractor; repeat
observations raise ``confidence``.  A ``manual`` memory comes from the user and
is never overwritten by auto extraction.  Forgetting is a soft delete
(``active = 0``) so a memory can be revived if re-observed.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

MEMORY_CATEGORIES = (
    "category",
    "color",
    "style",
    "formality",
    "occasion",
    "habit",
    "general",
)

AUTO_BASE_CONFIDENCE = 0.35
AUTO_CONFIDENCE_STEP = 0.25
AUTO_CONFIDENCE_FLOOR = 0.1
AUTO_CONFIDENCE_MAX = 1.0


def _confidence_for(occurrences: int) -> float:
    """confidence = 0.25 * occurrences + 0.1 (capped): 1->0.35, 2->0.6, 3->0.85, 4->1.0."""
    return min(AUTO_CONFIDENCE_MAX, AUTO_CONFIDENCE_STEP * occurrences + AUTO_CONFIDENCE_FLOOR)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_category(category: str) -> str:
    if category not in MEMORY_CATEGORIES:
        raise ValueError(f"invalid memory category: {category}")
    return category


def normalize_content(content: str) -> str:
    text = " ".join(str(content).strip().lower().split())
    return text[:64]


def _row_to_memory(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "memory_id": row["memory_id"],
        "user_id": row["user_id"],
        "source": row["source"],
        "category": row["category"],
        "content": row["content"],
        "confidence": row["confidence"],
        "occurrences": row["occurrences"],
        "meta": json.loads(row["meta_json"]) if row["meta_json"] else {},
        "active": bool(row["active"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_memories(
    connection: sqlite3.Connection,
    user_id: str,
    *,
    include_inactive: bool = False,
    category: str | None = None,
) -> list[dict[str, Any]]:
    if category is not None:
        _validate_category(category)
    rows = connection.execute(
        f"""
        SELECT memory_id, user_id, source, category, content, confidence,
               occurrences, meta_json, active, created_at, updated_at
        FROM user_memories
        WHERE user_id = ?
          {'AND active = 1' if not include_inactive else ''}
          {'AND category = ?' if category is not None else ''}
        ORDER BY updated_at DESC, memory_id DESC
        """,  # noqa: S608
        (user_id, *((category,) if category is not None else ())),
    ).fetchall()
    return [_row_to_memory(row) for row in rows]


def create_manual_memory(
    connection: sqlite3.Connection,
    user_id: str,
    category: str,
    content: str,
    *,
    confidence: float = 1.0,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not user_id.strip():
        raise ValueError("user_id cannot be empty")
    _validate_category(category)
    content = normalize_content(content)
    if not content:
        raise ValueError("memory content cannot be empty")
    if not 0.0 <= float(confidence) <= 1.0:
        raise ValueError("confidence must be between 0 and 1")
    timestamp = _now()
    cursor = connection.execute(
        """
        INSERT INTO user_memories(
            user_id, source, category, content, confidence, occurrences,
            meta_json, active, created_at, updated_at
        ) VALUES (?, 'manual', ?, ?, ?, 0, ?, 1, ?, ?)
        ON CONFLICT(user_id, category, content) DO UPDATE SET
            source = 'manual',
            confidence = excluded.confidence,
            meta_json = excluded.meta_json,
            occurrences = 0,
            active = 1,
            updated_at = excluded.updated_at
        """,
        (
            user_id,
            category,
            content,
            float(confidence),
            json.dumps(meta or {}, ensure_ascii=False),
            timestamp,
            timestamp,
        ),
    )
    memory_id = cursor.lastrowid
    row = connection.execute(
        """
        SELECT memory_id, user_id, source, category, content, confidence,
               occurrences, meta_json, active, created_at, updated_at
        FROM user_memories
        WHERE memory_id = ?
        """,
        (memory_id,),
    ).fetchone()
    if row is None:
        # Upserted an existing row: lastrowid points at the new id that may
        # not exist; fall back to re-selecting by the unique key.
        row = connection.execute(
            """
            SELECT memory_id, user_id, source, category, content, confidence,
                   occurrences, meta_json, active, created_at, updated_at
            FROM user_memories
            WHERE user_id = ? AND category = ? AND content = ?
            """,
            (user_id, category, content),
        ).fetchone()
    return _row_to_memory(row)


def update_memory(
    connection: sqlite3.Connection,
    user_id: str,
    memory_id: int,
    *,
    category: str | None = None,
    content: str | None = None,
    confidence: float | None = None,
) -> dict[str, Any] | None:
    if category is not None:
        _validate_category(category)
    if content is not None:
        content = normalize_content(content)
        if not content:
            raise ValueError("memory content cannot be empty")
    if confidence is not None and not 0.0 <= float(confidence) <= 1.0:
        raise ValueError("confidence must be between 0 and 1")
    if category is None and content is None and confidence is None:
        raise ValueError("nothing to update")
    assignments = []
    parameters: list[Any] = []
    if category is not None:
        assignments.append("category = ?")
        parameters.append(category)
    if content is not None:
        assignments.append("content = ?")
        parameters.append(content)
    if confidence is not None:
        assignments.append("confidence = ?")
        parameters.append(float(confidence))
    assignments.append("updated_at = ?")
    parameters.append(_now())
    parameters.extend([user_id, memory_id])
    row = connection.execute(
        f"""
        UPDATE user_memories
        SET {', '.join(assignments)}
        WHERE user_id = ? AND memory_id = ?
        RETURNING memory_id, user_id, source, category, content, confidence,
                  occurrences, meta_json, active, created_at, updated_at
        """,  # noqa: S608
        parameters,
    ).fetchone()
    return _row_to_memory(row) if row is not None else None


def forget_memory(
    connection: sqlite3.Connection,
    user_id: str,
    memory_id: int,
) -> bool:
    cursor = connection.execute(
        "UPDATE user_memories SET active = 0, updated_at = ? WHERE user_id = ? AND memory_id = ?",
        (_now(), user_id, memory_id),
    )
    return cursor.rowcount > 0


def active_memory_profile(
    connection: sqlite3.Connection,
    user_id: str,
    limit: int = 30,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT memory_id, user_id, source, category, content, confidence,
               occurrences, meta_json, active, created_at, updated_at
        FROM user_memories
        WHERE user_id = ? AND active = 1
        ORDER BY confidence DESC, updated_at DESC
        LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
    return [_row_to_memory(row) for row in rows]


def upsert_auto_memories(
    connection: sqlite3.Connection,
    user_id: str,
    extracts: list[dict[str, Any]],
) -> int:
    """Distill one observation into auto memories, honoring manual priority.

    Each extract must be ``{"category", "content", "meta"}``.  A new auto
    memory starts at ``AUTO_BASE_CONFIDENCE`` and each further observation
    adds ``AUTO_CONFIDENCE_STEP`` (capped at 1.0).  Manual memories are never
    overwritten.  A forgotten memory is revived (``active = 1``) when observed
    again.
    """
    if not user_id.strip():
        raise ValueError("user_id cannot be empty")
    processed = 0
    timestamp = _now()
    for extract in extracts:
        category = _validate_category(str(extract.get("category", "general")))
        content = normalize_content(str(extract.get("content", "")))
        if not content:
            continue
        meta = extract.get("meta") or {}
        row = connection.execute(
            """
            SELECT memory_id, source, occurrences, confidence
            FROM user_memories
            WHERE user_id = ? AND category = ? AND content = ?
            """,
            (user_id, category, content),
        ).fetchone()
        if row is None:
            connection.execute(
                """
                INSERT INTO user_memories(
                    user_id, source, category, content, confidence, occurrences,
                    meta_json, active, created_at, updated_at
                ) VALUES (?, 'auto', ?, ?, ?, 1, ?, 1, ?, ?)
                """,
                (
                    user_id,
                    category,
                    content,
                    AUTO_BASE_CONFIDENCE,
                    json.dumps(meta, ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )
        elif row["source"] == "manual":
            continue
        else:
            occurrences = int(row["occurrences"]) + 1
            confidence = _confidence_for(occurrences)
            connection.execute(
                """
                UPDATE user_memories
                SET occurrences = ?, confidence = ?, active = 1, updated_at = ?
                WHERE memory_id = ?
                """,
                (occurrences, confidence, timestamp, row["memory_id"]),
            )
        processed += 1
    return processed
