"""Session-end consolidation of preference evidence.

The scheme wants the past to stay reproducible, so consolidation *never*
summarizes raw evidence into prose; instead it (1) de-duplicates evidence that
records the same claim at different strengths, and (2) re-runs the idempotent
aggregator over every affected preference key so counters and lifecycle match
the surviving evidence. This keeps the model self-consistent without a
background job — callers invoke it when a chat session ends.
"""

from __future__ import annotations

from typing import Any

from styleforge.repositories import preference_evidence_repository
from styleforge.services.memory_aggregator import recompute_preference


def _dedupe_key(evidence: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        evidence.get("dimension") or "",
        evidence.get("attribute") or "",
        evidence.get("value") or "",
        evidence.get("polarity") or "",
    )


def consolidate_session(
    connection: Any,
    user_id: str,
    session_id: str = "",
    *,
    limit: int = 1000,
) -> dict[str, Any]:
    """De-duplicate a user's evidence and recompute the affected preferences.

    Returns ``{"deduped": int, "recomputed": int}``. ``session_id`` is accepted
    for future session-scoped filtering; evidence is currently de-duplicated
    per user.
    """
    rows = preference_evidence_repository.list_evidence(connection, user_id, limit=limit)
    best: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = _dedupe_key(row)
        current = best.get(key)
        if current is None or row["strength"] > current["strength"]:
            best[key] = row

    removed: set[int] = set()
    for row in rows:
        keeper = best[_dedupe_key(row)]
        if row["evidence_id"] != keeper["evidence_id"]:
            removed.add(row["evidence_id"])

    for evidence_id in removed:
        preference_evidence_repository.delete_evidence(connection, evidence_id)

    affected: dict[tuple[str, str, str], None] = {}
    for row in best.values():
        affected[
            (row.get("dimension") or "", row["attribute"], row["value"])
        ] = None
    for (dimension, attribute, value) in affected:
        recompute_preference(connection, user_id, dimension, attribute, value)

    return {"deduped": len(removed), "recomputed": len(affected)}


__all__ = ["consolidate_session"]
