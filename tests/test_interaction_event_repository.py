"""Tests for the raw behavior-event repository (the fact layer)."""

from __future__ import annotations

import pytest

from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.interaction_event_repository import (
    EVENT_TYPES,
    list_events,
    record_event,
)


def test_record_and_list_events(db_dsn: str) -> None:
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        record_event(
            connection,
            "u",
            "outfit_selected",
            context={"outfit_id": "o1", "item_ids": ["i1", "i2"]},
        )
        record_event(connection, "u", "outfit_rejected", item_id="i1")
        events = list_events(connection, "u")
    assert len(events) == 2
    # Newest first (event_id DESC breaks same-timestamp ties).
    assert events[0]["event_type"] == "outfit_rejected"
    assert events[0]["item_id"] == "i1"
    assert events[1]["event_type"] == "outfit_selected"
    assert events[1]["context"]["item_ids"] == ["i1", "i2"]


def test_list_events_filters_by_type(db_dsn: str) -> None:
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        record_event(connection, "u", "outfit_selected")
        record_event(
            connection, "u", "feedback_submitted", features={"feedback": "positive"}
        )
        selected = list_events(connection, "u", event_types=("outfit_selected",))
        assert [event["event_type"] for event in selected] == ["outfit_selected"]
        feedback = list_events(connection, "u", event_types=("feedback_submitted",))
        assert feedback[0]["features"]["feedback"] == "positive"


def test_record_event_rejects_unknown_type(db_dsn: str) -> None:
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        with pytest.raises(ValueError, match="invalid event type"):
            record_event(connection, "u", "nope")


def test_events_are_user_scoped(db_dsn: str) -> None:
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        record_event(connection, "u1", "outfit_selected")
        record_event(connection, "u2", "outfit_selected")
        assert len(list_events(connection, "u1")) == 1
        assert len(list_events(connection, "u2")) == 1


def test_event_types_cover_behavior_surface(db_dsn: str) -> None:
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        for event_type in EVENT_TYPES:
            record_event(connection, "u", event_type)
        events = list_events(connection, "u", limit=100)
    assert {event["event_type"] for event in events} == set(EVENT_TYPES)
