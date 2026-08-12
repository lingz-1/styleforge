"""Tests for the dimensioned preference-model repository."""

from __future__ import annotations

import pytest

from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.preference_model_repository import (
    get_preference,
    get_preference_by_key,
    list_preferences,
    soft_forget,
    upsert_preference,
)


def test_upsert_preference_by_unique_key(db_dsn: str) -> None:
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        first = upsert_preference(
            connection, "u", dimension="style", attribute="style", value="简约", confidence=0.8
        )
        second = upsert_preference(
            connection, "u", dimension="style", attribute="style", value="简约", confidence=0.95
        )
        # Same (user, dimension, attribute, value) key updates, never duplicates.
        assert first["preference_id"] == second["preference_id"]
        assert second["confidence"] == pytest.approx(0.95)
        assert len(list_preferences(connection, "u")) == 1


def test_list_preferences_filters_lifecycle_and_orders(db_dsn: str) -> None:
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        upsert_preference(
            connection, "u", dimension="style", attribute="style", value="简约",
            lifecycle="long_term", confidence=0.9,
        )
        upsert_preference(
            connection, "u", dimension="style", attribute="formality", value="正式",
            lifecycle="short_term", confidence=0.5,
        )
        long_term = list_preferences(connection, "u", lifecycle="long_term")
        assert len(long_term) == 1
        assert long_term[0]["value"] == "简约"


def test_get_preference_and_soft_forget(db_dsn: str) -> None:
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        pref = upsert_preference(
            connection, "u", dimension="garment", attribute="category", value="coat"
        )
        assert get_preference(connection, "u", pref["preference_id"])["value"] == "coat"
        assert get_preference_by_key(connection, "u", "garment", "category", "coat")["active"] is True

        # Soft forget hides it from active listings but keeps the row (revivable).
        assert soft_forget(connection, "u", pref["preference_id"])
        assert list_preferences(connection, "u") == []
        assert get_preference_by_key(connection, "u", "garment", "category", "coat")["active"] is False
        assert not soft_forget(connection, "u", 99999)


def test_upsert_preference_validates_inputs(db_dsn: str) -> None:
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        with pytest.raises(ValueError, match="attribute"):
            upsert_preference(connection, "u", dimension="style", attribute=" ", value="x")
        with pytest.raises(ValueError, match="polarity"):
            upsert_preference(connection, "u", dimension="style", attribute="style", value="x", polarity="nope")
        with pytest.raises(ValueError, match="lifecycle"):
            upsert_preference(connection, "u", dimension="style", attribute="style", value="x", lifecycle="forever")
        with pytest.raises(ValueError, match="decay_policy"):
            upsert_preference(connection, "u", dimension="style", attribute="style", value="x", decay_policy="fast")
