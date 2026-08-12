"""Tests for session-end evidence consolidation (de-dup + re-aggregate)."""

from __future__ import annotations

import pytest

from styleforge.repositories import preference_evidence_repository
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.preference_model_repository import get_preference_by_key
from styleforge.services.memory_aggregator import apply_evidence
from styleforge.services.memory_consolidation import consolidate_session


def _evidence(
    value: str,
    strength: float,
    polarity: str = "positive",
    scope: dict | None = None,
) -> dict:
    return {
        "dimension": "garment",
        "attribute": "category",
        "value": value,
        "polarity": polarity,
        "strength": strength,
        "scope": scope or {"type": "global"},
        "source": "llm_request",
    }


def test_consolidation_dedupes_and_recomputes(db_dsn: str) -> None:
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        apply_evidence(connection, "u", [_evidence("shirt", 0.4)])
        apply_evidence(connection, "u", [_evidence("shirt", 0.9)])
        assert len(preference_evidence_repository.list_evidence(connection, "u")) == 2

        result = consolidate_session(connection, "u")
        assert result["deduped"] == 1
        assert result["recomputed"] == 1

        remaining = preference_evidence_repository.list_evidence(connection, "u")
        assert len(remaining) == 1
        # The strongest duplicate survives.
        assert remaining[0]["strength"] == pytest.approx(0.9)

        # Aggregation was re-run: counters match the surviving evidence only.
        pref = get_preference_by_key(connection, "u", "garment", "category", "shirt")
        assert pref["support_count"] == 1
        assert pref["support_score"] == pytest.approx(0.9)


def test_consolidation_keeps_distinct_claims(db_dsn: str) -> None:
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        apply_evidence(connection, "u", [_evidence("shirt", 0.5)])
        apply_evidence(connection, "u", [
            {
                "dimension": "style",
                "attribute": "style",
                "value": "minimal",
                "polarity": "positive",
                "strength": 0.5,
                "scope": {"type": "global"},
                "source": "llm_request",
            }
        ])
        result = consolidate_session(connection, "u")
        # Different keys are never merged.
        assert result["deduped"] == 0
        assert result["recomputed"] == 2
        assert len(preference_evidence_repository.list_evidence(connection, "u")) == 2


def test_consolidation_is_idempotent_on_rerun(db_dsn: str) -> None:
    """Running consolidation twice must be a no-op the second time."""
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        apply_evidence(connection, "u", [_evidence("shirt", 0.4)])
        apply_evidence(connection, "u", [_evidence("shirt", 0.9)])

        first = consolidate_session(connection, "u")
        assert first["deduped"] == 1
        assert first["recomputed"] == 1

        second = consolidate_session(connection, "u")
        assert second["deduped"] == 0
        # The affected key is still recomputed, but with the surviving evidence.
        assert second["recomputed"] == 1
        pref = get_preference_by_key(connection, "u", "garment", "category", "shirt")
        assert pref["support_count"] == 1
        assert pref["support_score"] == pytest.approx(0.9)


def test_consolidation_is_scoped_to_its_user(db_dsn: str) -> None:
    """Consolidating one user must not touch another user's evidence or model."""
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        apply_evidence(connection, "u1", [_evidence("shirt", 0.4), _evidence("shirt", 0.9)])
        apply_evidence(connection, "u2", [_evidence("coat", 0.6)])

        result = consolidate_session(connection, "u1")
        assert result["deduped"] == 1

        # u2 keeps every row and its aggregated counters unchanged.
        assert len(preference_evidence_repository.list_evidence(connection, "u2")) == 1
        pref = get_preference_by_key(connection, "u2", "garment", "category", "coat")
        assert pref["support_count"] == 1
        assert pref["support_score"] == pytest.approx(0.6)


def test_consolidation_never_merges_opposite_polarities(db_dsn: str) -> None:
    """Positive and negative claims on the same key are different claims: both
    must survive consolidation (the contradiction is data, not a duplicate)."""
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        apply_evidence(connection, "u", [
            _evidence("shirt", 0.5, polarity="positive"),
            _evidence("shirt", 0.5, polarity="negative"),
        ])
        result = consolidate_session(connection, "u")
        assert result["deduped"] == 0
        assert result["recomputed"] == 1
        remaining = preference_evidence_repository.list_evidence(connection, "u")
        assert len(remaining) == 2
        pref = get_preference_by_key(connection, "u", "garment", "category", "shirt")
        assert pref["support_count"] == 1
        assert pref["contradiction_count"] == 1
