"""Canonical response contract for outfit recommendation results."""

from __future__ import annotations

from typing import Any, Final


OUTFIT_RECOMMEND_RESULT_SCHEMA: Final = "styleforge.outfit-recommend-result.v1"


def normalize_outfit_recommend_result(
    result: dict[str, Any],
    *,
    run_id: str,
    status: str | None = None,
) -> dict[str, Any]:
    """Return one versioned result shape for every recommendation engine.

    ``structured_result.recommendations`` is canonical. The flat
    ``recommendations`` field remains as a deprecated compatibility alias so
    existing clients and persisted-session readers continue to work during the
    migration. If both forms are present, the canonical structured form wins.
    """
    payload = dict(result)
    structured_source = payload.get("structured_result")
    structured = (
        dict(structured_source) if isinstance(structured_source, dict) else {}
    )

    recommendations = structured.get("recommendations")
    if not isinstance(recommendations, list):
        recommendations = payload.get("recommendations")
    if not isinstance(recommendations, list):
        recommendations = []

    resolved_status = str(
        status
        or payload.get("status")
        or structured.get("status")
        or "infeasible"
    )
    structured.update(
        {
            "run_id": run_id,
            "status": resolved_status,
            "recommendations": recommendations,
        }
    )

    payload.update(
        {
            "schema_version": OUTFIT_RECOMMEND_RESULT_SCHEMA,
            "run_id": run_id,
            "status": resolved_status,
            "structured_result": structured,
            # Deprecated compatibility alias. Remove only in a future major
            # contract version after all clients and stored sessions migrate.
            "recommendations": recommendations,
        }
    )
    return payload
