"""Standardized preference evidence (the interpretation layer).

An evidence is a normalized claim extracted from either a user request (LLM) or
a raw behavior event (deterministic rules): ``attribute`` + ``value`` +
``polarity`` + ``strength`` + ``scope``. The aggregated preference model in
``preference_model`` is derived from these rows, never stored directly here.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from styleforge.repositories.database import Connection, Row

POLARITIES = ("positive", "negative")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_polarity(polarity: str) -> str:
    if polarity not in POLARITIES:
        raise ValueError(f"invalid polarity: {polarity}")
    return polarity


def _row_to_evidence(row: Row) -> dict[str, Any]:
    return {
        "evidence_id": row["evidence_id"],
        "user_id": row["user_id"],
        "dimension": row["dimension"],
        "attribute": row["attribute"],
        "value": row["value"],
        "polarity": row["polarity"],
        "strength": row["strength"],
        "scope": json.loads(row["scope_json"]) if row["scope_json"] else {},
        "source": row["source"],
        "event_id": row["event_id"],
        "created_at": row["created_at"],
    }


def add_evidence(
    connection: Connection,
    user_id: str,
    attribute: str,
    value: str,
    *,
    dimension: str = "",
    polarity: str = "positive",
    strength: float = 0.5,
    scope: dict[str, Any] | None = None,
    source: str = "llm_request",
    event_id: int | None = None,
) -> dict[str, Any]:
    """Append one evidence row and return it as a dict."""
    if not user_id.strip():
        raise ValueError("user_id cannot be empty")
    attribute = str(attribute).strip().lower()
    value = str(value).strip().lower()
    if not attribute or not value:
        raise ValueError("attribute and value cannot be empty")
    _validate_polarity(polarity)
    if not 0.0 <= float(strength) <= 1.0:
        raise ValueError("strength must be between 0 and 1")
    timestamp = _now()
    cursor = connection.execute(
        """
        INSERT INTO preference_evidence(
            user_id, dimension, attribute, value, polarity, strength,
            scope_json, source, event_id, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING evidence_id
        """,
        (
            user_id,
            dimension,
            attribute,
            value,
            polarity,
            float(strength),
            json.dumps(scope or {}, ensure_ascii=False),
            source,
            event_id,
            timestamp,
        ),
    )
    evidence_id = cursor.fetchone()["evidence_id"]
    row = connection.execute(
        """
        SELECT evidence_id, user_id, dimension, attribute, value, polarity,
               strength, scope_json, source, event_id, created_at
        FROM preference_evidence
        WHERE evidence_id = %s
        """,
        (evidence_id,),
    ).fetchone()
    return _row_to_evidence(row)


def list_evidence_for_key(
    connection: Connection,
    user_id: str,
    dimension: str,
    attribute: str,
    value: str,
    *,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """All evidence rows for one preference key, oldest-first.

    Used by the aggregator to recompute a preference row idempotently from its
    full evidence history (each call starts from the raw rows, never from a
    running counter, so consolidation can safely re-run it).
    """
    rows = connection.execute(
        """
        SELECT evidence_id, user_id, dimension, attribute, value, polarity,
               strength, scope_json, source, event_id, created_at
        FROM preference_evidence
        WHERE user_id = %s AND dimension = %s AND attribute = %s AND value = %s
        ORDER BY created_at ASC, evidence_id ASC
        LIMIT %s
        """,
        (user_id, dimension, attribute, value, limit),
    ).fetchall()
    return [_row_to_evidence(row) for row in rows]


def list_evidence(
    connection: Connection,
    user_id: str,
    *,
    attribute: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Newest-first evidence rows for a user, optionally filtered by attribute."""
    if attribute is not None:
        rows = connection.execute(
            """
            SELECT evidence_id, user_id, dimension, attribute, value, polarity,
                   strength, scope_json, source, event_id, created_at
            FROM preference_evidence
            WHERE user_id = %s AND attribute = %s
            ORDER BY created_at DESC, evidence_id DESC
            LIMIT %s
            """,
            (user_id, attribute, limit),
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT evidence_id, user_id, dimension, attribute, value, polarity,
                   strength, scope_json, source, event_id, created_at
            FROM preference_evidence
            WHERE user_id = %s
            ORDER BY created_at DESC, evidence_id DESC
            LIMIT %s
            """,
            (user_id, limit),
        ).fetchall()
    return [_row_to_evidence(row) for row in rows]


def delete_evidence(connection: Connection, evidence_id: int) -> bool:
    """Hard-delete one evidence row (used by consolidation to de-duplicate)."""
    cursor = connection.execute(
        "DELETE FROM preference_evidence WHERE evidence_id = %s",
        (evidence_id,),
    )
    return cursor.rowcount > 0


__all__ = [
    "POLARITIES",
    "add_evidence",
    "list_evidence",
    "list_evidence_for_key",
    "delete_evidence",
]
