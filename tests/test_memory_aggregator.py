"""Tests for the deterministic evidence aggregator (support/contradiction)."""

from __future__ import annotations

import pytest

from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.preference_evidence_repository import (
    delete_evidence,
    list_evidence,
)
from styleforge.repositories.preference_model_repository import get_preference_by_key
from styleforge.services.memory_aggregator import apply_evidence, recompute_preference


def _evidence(
    attribute: str,
    value: str,
    polarity: str = "positive",
    strength: float = 0.5,
    dimension: str = "garment",
    source: str = "llm_request",
    scope: dict | None = None,
) -> dict:
    return {
        "dimension": dimension,
        "attribute": attribute,
        "value": value,
        "polarity": polarity,
        "strength": strength,
        "scope": scope or {"type": "global"},
        "source": source,
    }


def test_apply_evidence_builds_preference_row(db_dsn: str) -> None:
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        updated = apply_evidence(connection, "u", [_evidence("category", "shirt", strength=0.6)])
        assert len(updated) == 1
        pref = get_preference_by_key(connection, "u", "garment", "category", "shirt")
        assert pref["polarity"] == "positive"
        assert pref["support_count"] == 1
        assert pref["contradiction_count"] == 0
        assert pref["support_score"] == pytest.approx(0.6)
        # confidence = 0.2 + 0.55 * 0.6/(0.6+0.5) + 0.25 * min(1,5)/5
        assert pref["confidence"] == pytest.approx(0.2 + 0.55 * 0.6 / 1.1 + 0.05)
        assert pref["lifecycle"] == "short_term"
        assert pref["decay_policy"] == "normal"
        assert pref["expires_at"]
        # Evidence rows persist alongside the aggregated model.
        assert len(list_evidence(connection, "u")) == 1


def test_contradiction_flips_polarity(db_dsn: str) -> None:
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        apply_evidence(connection, "u", [_evidence("category", "shirt", strength=0.5)])
        apply_evidence(
            connection,
            "u",
            [
                _evidence("category", "shirt", polarity="negative", strength=0.9),
                _evidence("category", "shirt", polarity="negative", strength=0.9),
            ],
        )
        pref = get_preference_by_key(connection, "u", "garment", "category", "shirt")
        assert pref["polarity"] == "negative"
        assert pref["support_count"] == 1
        assert pref["contradiction_count"] == 2
        assert pref["contradiction_score"] == pytest.approx(1.8)


def test_lifecycle_upgrades_on_repeated_support(db_dsn: str) -> None:
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        for _ in range(6):
            apply_evidence(connection, "u", [_evidence("category", "coat")])
        pref = get_preference_by_key(connection, "u", "garment", "category", "coat")
        assert pref["lifecycle"] == "long_term"
        assert pref["support_count"] == 6
        assert pref["decay_policy"] == "slow"
        assert pref["expires_at"] == ""


def test_explicit_source_promotes_directly_to_long_term(db_dsn: str) -> None:
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        apply_evidence(
            connection,
            "u",
            [_evidence("category", "leather", source="explicit_statement", strength=0.9)],
        )
        pref = get_preference_by_key(connection, "u", "garment", "category", "leather")
        assert pref["lifecycle"] == "long_term"
        assert pref["decay_policy"] == "none"


def test_contextual_scope_is_persisted(db_dsn: str) -> None:
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        scope = {"type": "contextual", "occasions": ["通勤"]}
        apply_evidence(
            connection,
            "u",
            [_evidence("style", "minimal", dimension="style", scope=scope)],
        )
        pref = get_preference_by_key(connection, "u", "style", "style", "minimal")
        assert pref["scope"] == scope


def test_recompute_with_no_evidence_forgets_row(db_dsn: str) -> None:
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        apply_evidence(connection, "u", [_evidence("category", "coat")])
        row = list_evidence(connection, "u")[0]
        delete_evidence(connection, row["evidence_id"])
        result = recompute_preference(connection, "u", "garment", "category", "coat")
        assert result == {}
        assert get_preference_by_key(connection, "u", "garment", "category", "coat")["active"] is False
