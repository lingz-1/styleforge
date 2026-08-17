"""Persistence for the user's saved-outfit collection (SaveOutfit command).

Saving is a command/event, not a long-lived workflow state: ``save_outfit``
persists an outfit snapshot keyed by (user_id, outfit_id) so the user can
revisit it later. There is deliberately no ``confirmed_outfit`` intermediate
state in the Agentic contract — an outfit is either being edited (active) or
has been saved (this table).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from styleforge.repositories.database import Connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_outfit(
    connection: Connection,
    user_id: str,
    outfit_id: str,
    item_ids: list[str],
    source_run_id: str = "",
) -> None:
    """Upsert one saved outfit. Re-saving an outfit updates its snapshot."""
    connection.execute(
        """
        INSERT INTO saved_outfits(user_id, outfit_id, item_ids_json, source_run_id, created_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT(user_id, outfit_id) DO UPDATE SET
            item_ids_json = excluded.item_ids_json,
            source_run_id = excluded.source_run_id,
            created_at = excluded.created_at
        """,
        (
            user_id,
            outfit_id,
            json.dumps(list(item_ids), ensure_ascii=False),
            source_run_id,
            _now(),
        ),
    )


def list_saved_outfits(connection: Connection, user_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT outfit_id, item_ids_json, source_run_id, created_at
        FROM saved_outfits
        WHERE user_id = %s
        ORDER BY created_at DESC
        """,
        (user_id,),
    ).fetchall()
    return [
        {
            "outfit_id": row["outfit_id"],
            "item_ids": json.loads(row["item_ids_json"] or "[]"),
            "source_run_id": row["source_run_id"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def delete_saved_outfit(connection: Connection, user_id: str, outfit_id: str) -> None:
    connection.execute(
        "DELETE FROM saved_outfits WHERE user_id = %s AND outfit_id = %s",
        (user_id, outfit_id),
    )
