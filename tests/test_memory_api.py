"""Tests for the preference-memory management API and behavior-event endpoint."""

from __future__ import annotations

import importlib
import sys

from fastapi.testclient import TestClient


def _client(db_dsn, monkeypatch):
    monkeypatch.setenv("STYLEFORGE_DATABASE_DSN", db_dsn)
    sys.modules.pop("styleforge.api", None)
    api = importlib.import_module("styleforge.api")
    return TestClient(api.app)


def test_preference_memory_crud_over_http(db_dsn, monkeypatch) -> None:
    with _client(db_dsn, monkeypatch) as client:
        created = client.post(
            "/preferences/u/memories",
            json={
                "dimension": "style",
                "attribute": "style",
                "value": "简约",
                "polarity": "positive",
                "strength": 0.9,
            },
        )
        assert created.status_code == 201
        preference = created.json()["preference"]
        assert preference["attribute"] == "style"
        assert preference["value"] == "简约"
        assert preference["polarity"] == "positive"
        # Explicit statements are promoted straight to long-term.
        assert preference["lifecycle"] == "long_term"
        assert preference["decay_policy"] == "none"

        listed = client.get("/preferences/u/memories")
        assert listed.status_code == 200
        assert listed.json()["count"] == 1
        assert listed.json()["memories"][0]["preference_id"] == preference["preference_id"]

        updated = client.patch(
            f"/preferences/u/memories/{preference['preference_id']}",
            json={"polarity": "negative", "lifecycle": "short_term"},
        )
        assert updated.status_code == 200
        assert updated.json()["polarity"] == "negative"
        assert updated.json()["lifecycle"] == "short_term"

        forgotten = client.delete(f"/preferences/u/memories/{preference['preference_id']}")
        assert forgotten.status_code == 200
        assert client.get("/preferences/u/memories").json()["count"] == 0


def test_behavior_event_endpoint(db_dsn, monkeypatch) -> None:
    with _client(db_dsn, monkeypatch) as client:
        created = client.post(
            "/users/u/events",
            json={
                "event_type": "outfit_selected",
                "context": {"request": "通勤推荐", "outfit_id": "o1", "item_ids": ["i1"]},
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["event_type"] == "outfit_selected"
        assert body["context"]["outfit_id"] == "o1"

        invalid = client.post(
            "/users/u/events",
            json={"event_type": "not-an-event"},
        )
        assert invalid.status_code == 422


def test_memory_api_validates_and_checks_ownership(db_dsn, monkeypatch) -> None:
    with _client(db_dsn, monkeypatch) as client:
        bad = client.post(
            "/preferences/u/memories",
            json={"dimension": "style", "attribute": "style", "value": ""},
        )
        assert bad.status_code == 422

        bad_polarity = client.post(
            "/preferences/u/memories",
            json={"dimension": "style", "attribute": "style", "value": "简约", "polarity": "nope"},
        )
        assert bad_polarity.status_code == 422

        missing = client.delete("/preferences/u/memories/99999")
        assert missing.status_code == 404

        missing_patch = client.patch(
            "/preferences/u/memories/99999", json={"polarity": "positive"}
        )
        assert missing_patch.status_code == 404


def test_behavior_event_records_deterministic_evidence(db_dsn, monkeypatch) -> None:
    """A single replaced piece folds to the item level, never a global category claim."""
    from styleforge.repositories.catalog_repository import upsert_items
    from styleforge.repositories.database import database_session, initialize_database

    from tests.helpers import make_item

    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        # Seed a catalog item so the evidence lookup has a category to attach to.
        upsert_items(
            connection, [make_item("coat-1", "coat", "灰色大衣", "gray")], "test"
        )

    with _client(db_dsn, monkeypatch) as client:
        created = client.post(
            "/users/u/events",
            json={
                "event_type": "item_replaced",
                "context": {"request": "换掉这件大衣"},
                "features": {"replaced_item_ids": ["coat-1"]},
            },
        )
        assert created.status_code == 201

        listed = client.get("/preferences/u/memories")
        assert listed.status_code == 200
        memories = listed.json()["memories"]
        # item-level + color-attribution rows, but no category row for one piece.
        assert len(memories) == 2
        assert any(
            m["attribute"] == "item"
            and m["value"] == "coat-1"
            and m["polarity"] == "negative"
            and m["scope"]["type"] == "contextual"
            for m in memories
        )
        assert any(
            m["attribute"] == "color" and m["value"] == "gray" for m in memories
        )
        assert not any(m["attribute"] == "category" for m in memories)
