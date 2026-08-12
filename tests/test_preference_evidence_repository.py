"""Tests for the standardized preference-evidence repository."""

from __future__ import annotations

import pytest

from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.preference_evidence_repository import (
    add_evidence,
    delete_evidence,
    list_evidence,
    list_evidence_for_key,
)


def test_add_and_list_evidence(db_dsn: str) -> None:
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        row = add_evidence(
            connection,
            "u",
            "category",
            "shirt",
            dimension="garment",
            strength=0.5,
            scope={"type": "contextual", "occasions": ["通勤"]},
            source="llm_request",
        )
        assert row["attribute"] == "category"
        assert row["value"] == "shirt"
        assert row["dimension"] == "garment"
        assert row["scope"]["occasions"] == ["通勤"]

        all_rows = list_evidence(connection, "u")
        assert len(all_rows) == 1
        key_rows = list_evidence_for_key(connection, "u", "garment", "category", "shirt")
        assert len(key_rows) == 1
        assert key_rows[0]["strength"] == pytest.approx(0.5)


def test_evidence_normalizes_attribute_and_value(db_dsn: str) -> None:
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        row = add_evidence(connection, "u", "  ColorFamily ", " Black ", strength=0.8)
        assert row["attribute"] == "colorfamily"
        assert row["value"] == "black"


def test_list_evidence_filters_by_attribute(db_dsn: str) -> None:
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        add_evidence(connection, "u", "category", "shirt")
        add_evidence(connection, "u", "color", "black")
        filtered = list_evidence(connection, "u", attribute="color")
        assert [row["attribute"] for row in filtered] == ["color"]


def test_delete_evidence(db_dsn: str) -> None:
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        row = add_evidence(connection, "u", "category", "coat")
        assert delete_evidence(connection, row["evidence_id"])
        assert list_evidence(connection, "u") == []
        assert not delete_evidence(connection, row["evidence_id"])


def test_add_evidence_validates_inputs(db_dsn: str) -> None:
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        with pytest.raises(ValueError, match="attribute"):
            add_evidence(connection, "u", "  ", "x")
        with pytest.raises(ValueError, match="polarity"):
            add_evidence(connection, "u", "category", "shirt", polarity="nope")
        with pytest.raises(ValueError, match="strength"):
            add_evidence(connection, "u", "category", "shirt", strength=2.0)
