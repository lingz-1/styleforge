"""Dimensioned preference model (replaces the retired ``user_memories`` table).

A preference is one row keyed by ``(user_id, dimension, attribute, value)`` with
aggregated ``polarity``, ``confidence``, support/contradiction counters and a
lifecycle. The counters are fed by ``services.memory_aggregator`` from
``preference_evidence`` rows; this module only persists and reads the model.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from styleforge.repositories.database import Connection, Row

LIFECYCLES = ("short_term", "long_term_candidate", "long_term")
DECAY_POLICIES = ("none", "slow", "normal")
POLARITIES = ("positive", "negative")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_lifecycle(lifecycle: str) -> str:
    if lifecycle not in LIFECYCLES:
        raise ValueError(f"invalid lifecycle: {lifecycle}")
    return lifecycle


def _row_to_preference(row: Row) -> dict[str, Any]:
    return {
        "preference_id": row["preference_id"],
        "user_id": row["user_id"],
        "dimension": row["dimension"],
        "attribute": row["attribute"],
        "value": row["value"],
        "polarity": row["polarity"],
        "lifecycle": row["lifecycle"],
        "scope": json.loads(row["scope_json"]) if row["scope_json"] else {},
        "confidence": row["confidence"],
        "support_score": row["support_score"],
        "contradiction_score": row["contradiction_score"],
        "support_count": row["support_count"],
        "contradiction_count": row["contradiction_count"],
        "source_summary": json.loads(row["source_summary_json"])
        if row["source_summary_json"]
        else {},
        "decay_policy": row["decay_policy"],
        "last_observed_at": row["last_observed_at"],
        "expires_at": row["expires_at"],
        "active": bool(row["active"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


_SELECT_COLUMNS = (
    "preference_id, user_id, dimension, attribute, value, polarity, lifecycle, "
    "scope_json, confidence, support_score, contradiction_score, support_count, "
    "contradiction_count, source_summary_json, decay_policy, last_observed_at, "
    "expires_at, active, created_at, updated_at"
)


def get_preference_by_key(
    connection: Connection,
    user_id: str,
    dimension: str,
    attribute: str,
    value: str,
) -> dict[str, Any] | None:
    """Fetch one preference by its unique key, or ``None``."""
    row = connection.execute(
        f"""
        SELECT {_SELECT_COLUMNS}
        FROM preference_model
        WHERE user_id = %s AND dimension = %s AND attribute = %s AND value = %s
        """,
        (user_id, dimension, attribute, value),
    ).fetchone()
    return _row_to_preference(row) if row is not None else None


def get_preference(
    connection: Connection,
    user_id: str,
    preference_id: int,
) -> dict[str, Any] | None:
    """Fetch one preference row by its id (used by the management API)."""
    row = connection.execute(
        f"""
        SELECT {_SELECT_COLUMNS}
        FROM preference_model
        WHERE user_id = %s AND preference_id = %s
        """,
        (user_id, preference_id),
    ).fetchone()
    return _row_to_preference(row) if row is not None else None


def list_preferences(
    connection: Connection,
    user_id: str,
    *,
    include_inactive: bool = False,
    lifecycle: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Active preferences newest-first, optionally filtered by lifecycle."""
    if lifecycle is not None:
        _validate_lifecycle(lifecycle)
    clauses = ["user_id = %s"]
    parameters: list[Any] = [user_id]
    if not include_inactive:
        clauses.append("active = 1")
    if lifecycle is not None:
        clauses.append("lifecycle = %s")
        parameters.append(lifecycle)
    rows = connection.execute(
        f"""
        SELECT {_SELECT_COLUMNS}
        FROM preference_model
        WHERE {' AND '.join(clauses)}
        ORDER BY updated_at DESC, preference_id DESC
        LIMIT %s
        """,  # noqa: S608
        (*parameters, limit),
    ).fetchall()
    return [_row_to_preference(row) for row in rows]


def upsert_preference(
    connection: Connection,
    user_id: str,
    *,
    dimension: str,
    attribute: str,
    value: str,
    polarity: str = "positive",
    lifecycle: str = "short_term",
    scope: dict[str, Any] | None = None,
    confidence: float = 0.0,
    support_score: float = 0.0,
    contradiction_score: float = 0.0,
    support_count: int = 0,
    contradiction_count: int = 0,
    source_summary: dict[str, Any] | None = None,
    decay_policy: str = "normal",
    last_observed_at: str | None = None,
    expires_at: str = "",
) -> dict[str, Any]:
    """Insert or update a preference row by its unique key and return it."""
    if not user_id.strip():
        raise ValueError("user_id cannot be empty")
    attribute = str(attribute).strip().lower()
    value = str(value).strip().lower()
    if not attribute or not value:
        raise ValueError("attribute and value cannot be empty")
    if polarity not in POLARITIES:
        raise ValueError(f"invalid polarity: {polarity}")
    _validate_lifecycle(lifecycle)
    if decay_policy not in DECAY_POLICIES:
        raise ValueError(f"invalid decay_policy: {decay_policy}")
    timestamp = _now()
    observed_at = last_observed_at or timestamp
    cursor = connection.execute(
        f"""
        INSERT INTO preference_model(
            user_id, dimension, attribute, value, polarity, lifecycle, scope_json,
            confidence, support_score, contradiction_score, support_count,
            contradiction_count, source_summary_json, decay_policy,
            last_observed_at, expires_at, active, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)
        ON CONFLICT (user_id, dimension, attribute, value) DO UPDATE SET
            polarity = excluded.polarity,
            lifecycle = excluded.lifecycle,
            scope_json = excluded.scope_json,
            confidence = excluded.confidence,
            support_score = excluded.support_score,
            contradiction_score = excluded.contradiction_score,
            support_count = excluded.support_count,
            contradiction_count = excluded.contradiction_count,
            source_summary_json = excluded.source_summary_json,
            decay_policy = excluded.decay_policy,
            last_observed_at = excluded.last_observed_at,
            expires_at = excluded.expires_at,
            active = 1,
            updated_at = excluded.updated_at
        RETURNING {_SELECT_COLUMNS}
        """,  # noqa: S608
        (
            user_id,
            dimension,
            attribute,
            value,
            polarity,
            lifecycle,
            json.dumps(scope or {}, ensure_ascii=False),
            float(confidence),
            float(support_score),
            float(contradiction_score),
            int(support_count),
            int(contradiction_count),
            json.dumps(source_summary or {}, ensure_ascii=False),
            decay_policy,
            observed_at,
            expires_at,
            timestamp,
            timestamp,
        ),
    )
    return _row_to_preference(cursor.fetchone())


def soft_forget(
    connection: Connection,
    user_id: str,
    preference_id: int,
) -> bool:
    """Soft-delete one preference (revivable if observed again)."""
    cursor = connection.execute(
        "UPDATE preference_model SET active = 0, updated_at = %s "
        "WHERE user_id = %s AND preference_id = %s",
        (_now(), user_id, preference_id),
    )
    return cursor.rowcount > 0


__all__ = [
    "LIFECYCLES",
    "DECAY_POLICIES",
    "get_preference_by_key",
    "get_preference",
    "list_preferences",
    "upsert_preference",
    "soft_forget",
]
