"""Raw user behavior events (the fact layer of the preference memory system).

Every observable user action (adopting an item, replacing a piece, submitting
feedback, ...) is first recorded here as an uninterpreted fact. Interpretations
(evidence) are derived separately in ``services.memory_evidence`` and never
overwrite this event log, so the past stays reproducible.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from styleforge.repositories.database import Connection, Row

EVENT_TYPES = (
    "outfit_selected",
    "outfit_rejected",
    "item_replaced",
    "item_rejected",
    "feedback_submitted",
    "style_requested",
    "compatibility_checked",
    "explicit_preference",
    "wardrobe_adopted",
    "wardrobe_removed",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_event_type(event_type: str) -> str:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"invalid event type: {event_type}")
    return event_type


def _row_to_event(row: Row) -> dict[str, Any]:
    return {
        "event_id": row["event_id"],
        "user_id": row["user_id"],
        "event_type": row["event_type"],
        "item_id": row["item_id"],
        "context": json.loads(row["context_json"]) if row["context_json"] else {},
        "features": json.loads(row["features_json"]) if row["features_json"] else {},
        "created_at": row["created_at"],
    }


def record_event(
    connection: Connection,
    user_id: str,
    event_type: str,
    *,
    item_id: str = "",
    context: dict[str, Any] | None = None,
    features: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist one raw behavior event and return it as a dict."""
    if not user_id.strip():
        raise ValueError("user_id cannot be empty")
    _validate_event_type(event_type)
    timestamp = _now()
    cursor = connection.execute(
        """
        INSERT INTO interaction_events(
            user_id, event_type, item_id, context_json, features_json, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING event_id
        """,
        (
            user_id,
            event_type,
            item_id,
            json.dumps(context or {}, ensure_ascii=False),
            json.dumps(features or {}, ensure_ascii=False),
            timestamp,
        ),
    )
    event_id = cursor.fetchone()["event_id"]
    row = connection.execute(
        """
        SELECT event_id, user_id, event_type, item_id, context_json,
               features_json, created_at
        FROM interaction_events
        WHERE event_id = %s
        """,
        (event_id,),
    ).fetchone()
    return _row_to_event(row)


def list_events(
    connection: Connection,
    user_id: str,
    *,
    limit: int = 50,
    event_types: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Newest-first behavior events for a user, optionally filtered by type."""
    rows: list[Row] = []
    if event_types:
        for event_type in event_types:
            _validate_event_type(event_type)
        placeholders = ", ".join("%s" for _ in event_types)
        rows = connection.execute(
            f"""
            SELECT event_id, user_id, event_type, item_id, context_json,
                   features_json, created_at
            FROM interaction_events
            WHERE user_id = %s AND event_type IN ({placeholders})
            ORDER BY created_at DESC, event_id DESC
            LIMIT %s
            """,  # noqa: S608
            (user_id, *event_types, limit),
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT event_id, user_id, event_type, item_id, context_json,
                   features_json, created_at
            FROM interaction_events
            WHERE user_id = %s
            ORDER BY created_at DESC, event_id DESC
            LIMIT %s
            """,
            (user_id, limit),
        ).fetchall()
    return [_row_to_event(row) for row in rows]


__all__ = ["EVENT_TYPES", "record_event", "list_events"]
