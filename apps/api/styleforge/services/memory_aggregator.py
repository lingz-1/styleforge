"""Aggregate standardized evidence into the dimensioned preference model.

Per the scheme, aggregation is fully deterministic (LLM only interprets, this
code manages memory). Each preference row is *recomputed from its complete
evidence history* on every call — never from a running counter — so the update
is idempotent and consolidation can re-run it safely after de-duplicating
evidence.

Confidence follows the scheme's support/contradiction formula:
    confidence = clamp(0.2 + 0.55 * dominant/(dominant+other+0.5)
                       + 0.25 * min(dominant_count, 5)/5, 0, 1)
where ``dominant`` is the score of the net polarity side.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from styleforge.repositories import preference_evidence_repository, preference_model_repository

_SHORT_TERM_DAYS = 30
_CANDIDATE_DAYS = 90

_LONG_TERM_THRESHOLD = 6
_CANDIDATE_THRESHOLD = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _iso_plus_days(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _net_polarity(support_score: float, contradiction_score: float) -> str:
    return "positive" if support_score >= contradiction_score else "negative"


def _lifecycle_for(
    dominant_count: int,
    last_source: str,
    existing_lifecycle: str | None,
    contradiction_ratio: float,
) -> str:
    """Upgrade on repeated support; explicit statements go straight to long-term."""
    if last_source.startswith("explicit"):
        return "long_term"
    if dominant_count >= _LONG_TERM_THRESHOLD:
        return "long_term"
    if dominant_count >= _CANDIDATE_THRESHOLD:
        return "long_term_candidate"
    # Heavy contradiction erodes an established preference one step at a time.
    if existing_lifecycle in ("long_term", "long_term_candidate") and contradiction_ratio > 2.0:
        return "long_term_candidate" if existing_lifecycle == "long_term" else "short_term"
    return "short_term"


def _decay_policy_for(lifecycle: str, last_source: str) -> str:
    if lifecycle == "long_term":
        return "none" if last_source.startswith("explicit") else "slow"
    return "normal"


def _expires_at_for(lifecycle: str) -> str:
    if lifecycle == "short_term":
        return _iso_plus_days(_SHORT_TERM_DAYS)
    if lifecycle == "long_term_candidate":
        return _iso_plus_days(_CANDIDATE_DAYS)
    return ""


def _confidence_for(
    polarity: str,
    support_score: float,
    contradiction_score: float,
    dominant_count: int,
) -> float:
    if polarity == "negative":
        dominant = contradiction_score
        other = support_score
    else:
        dominant = support_score
        other = contradiction_score
    ratio = dominant / (dominant + other + 0.5)
    return _clamp(0.2 + 0.55 * ratio + 0.25 * min(dominant_count, 5) / 5)


def recompute_preference(
    connection: Any,
    user_id: str,
    dimension: str,
    attribute: str,
    value: str,
) -> dict[str, Any]:
    """Rebuild one preference row from its full evidence history."""
    rows = preference_evidence_repository.list_evidence_for_key(
        connection, user_id, dimension, attribute, value
    )
    if not rows:
        # No evidence left (e.g. consolidation removed duplicates): drop the row.
        existing = preference_model_repository.get_preference_by_key(
            connection, user_id, dimension, attribute, value
        )
        if existing is not None:
            preference_model_repository.soft_forget(connection, user_id, existing["preference_id"])
        return {}
    support_score = sum(r["strength"] for r in rows if r["polarity"] == "positive")
    contradiction_score = sum(r["strength"] for r in rows if r["polarity"] == "negative")
    support_count = sum(1 for r in rows if r["polarity"] == "positive")
    contradiction_count = sum(1 for r in rows if r["polarity"] == "negative")
    polarity = _net_polarity(support_score, contradiction_score)
    dominant_count = support_count if polarity == "positive" else contradiction_count
    contradiction_ratio = (
        contradiction_score / max(support_score, 1e-6)
        if polarity == "positive"
        else support_score / max(contradiction_score, 1e-6)
    )
    last_source = rows[-1]["source"]
    existing = preference_model_repository.get_preference_by_key(
        connection, user_id, dimension, attribute, value
    )
    existing_lifecycle = existing["lifecycle"] if existing is not None else None

    lifecycle = _lifecycle_for(dominant_count, last_source, existing_lifecycle, contradiction_ratio)
    confidence = _confidence_for(
        polarity, support_score, contradiction_score, dominant_count
    )
    source_summary = {"last_source": last_source, "samples": len(rows)}
    return preference_model_repository.upsert_preference(
        connection,
        user_id,
        dimension=dimension,
        attribute=attribute,
        value=value,
        polarity=polarity,
        lifecycle=lifecycle,
        scope=rows[-1].get("scope") or {"type": "global"},
        confidence=confidence,
        support_score=round(support_score, 4),
        contradiction_score=round(contradiction_score, 4),
        support_count=support_count,
        contradiction_count=contradiction_count,
        source_summary=source_summary,
        decay_policy=_decay_policy_for(lifecycle, last_source),
        last_observed_at=rows[-1]["created_at"],
        expires_at=_expires_at_for(lifecycle),
    )


_CONFLICT_PENALTY = 0.9


def _apply_conflict_penalty(
    connection: Any,
    user_id: str,
    preference: dict[str, Any],
) -> dict[str, Any] | None:
    """Soften a *global* preference that directly contradicts another value of
    the same attribute (e.g. ``style=极简 positive`` vs ``style=复古 negative``).

    Per the scheme, only "same attribute + same context + opposite preference"
    is a real conflict. Contextual preferences are already isolated by their
    scene, so this pass only looks at global rows. The tension is recorded in
    ``source_summary.conflict_with`` instead of deleting either row, and both
    sides lose a bit of confidence so neither reads as a strong global claim.
    Returns the updated row (or ``None`` when untouched) so the caller can
    detect changes.
    """
    if (preference.get("scope") or {}).get("type") != "global":
        return None
    rows = connection.execute(
        """
        SELECT value, polarity, scope_json FROM preference_model
        WHERE user_id = %s AND dimension = %s AND attribute = %s
          AND value <> %s AND active = 1
        """,
        (user_id, preference["dimension"], preference["attribute"], preference["value"]),
    ).fetchall()
    # Only a same-context (global) opposite is a real conflict; contextual rows
    # are isolated by their scene and must neither trigger nor receive a marker.
    conflicting = [
        row["value"]
        for row in rows
        if row["polarity"] != preference["polarity"]
        and (json.loads(row["scope_json"] or "{}") or {}).get("type") == "global"
    ]
    if not conflicting:
        return None
    source_summary = dict(preference.get("source_summary") or {})
    source_summary["conflict_with"] = conflicting
    return preference_model_repository.upsert_preference(
        connection,
        user_id,
        dimension=preference["dimension"],
        attribute=preference["attribute"],
        value=preference["value"],
        polarity=preference["polarity"],
        lifecycle=preference["lifecycle"],
        scope=preference.get("scope"),
        confidence=round(preference["confidence"] * _CONFLICT_PENALTY, 4),
        support_score=preference["support_score"],
        contradiction_score=preference["contradiction_score"],
        support_count=preference["support_count"],
        contradiction_count=preference["contradiction_count"],
        source_summary=source_summary,
        decay_policy=preference["decay_policy"],
        last_observed_at=preference["last_observed_at"],
        expires_at=preference["expires_at"],
    )


def apply_evidence(
    connection: Any,
    user_id: str,
    evidence_list: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Persist evidence rows, then recompute the affected preference rows.

    Returns the updated preference dicts (one per distinct key touched).
    """
    if not evidence_list:
        return []
    keys: dict[tuple[str, str, str], None] = {}
    for evidence in evidence_list:
        attribute = str(evidence.get("attribute") or "").strip().lower()
        value = str(evidence.get("value") or "").strip().lower()
        dimension = str(evidence.get("dimension") or "").strip().lower()
        if not attribute or not value:
            continue
        preference_evidence_repository.add_evidence(
            connection,
            user_id,
            attribute,
            value,
            dimension=dimension,
            polarity=evidence.get("polarity", "positive"),
            strength=float(evidence.get("strength") or 0.5),
            scope=evidence.get("scope") or {"type": "global"},
            source=evidence.get("source") or "llm_request",
            event_id=evidence.get("event_id"),
        )
        keys[(dimension, attribute, value)] = None
    updated: list[dict[str, Any]] = []
    attributes_affected: set[tuple[str, str]] = set()
    for (dimension, attribute, value) in keys:
        preference = recompute_preference(connection, user_id, dimension, attribute, value)
        if preference:
            updated.append(preference)
        attributes_affected.add((dimension, attribute))
    # Cross-value conflict pass: re-evaluate every active row of each affected
    # attribute so both sides of a global contradiction get the penalty and the
    # ``conflict_with`` marker, not just whichever row was written last.
    if attributes_affected:
        all_rows = preference_model_repository.list_preferences(
            connection, user_id, limit=1000
        )
        for row in all_rows:
            if (row["dimension"], row["attribute"]) in attributes_affected:
                _apply_conflict_penalty(connection, user_id, row)
    return updated


__all__ = ["apply_evidence", "recompute_preference"]
