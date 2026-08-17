"""HTTP tests for the SaveOutfit command (Agentic contract Stage 1).

Saving is a command/event, not a workflow state: ``POST /outfits/save``
persists an outfit snapshot (upsert keyed by user_id+outfit_id) and fires an
``outfit_selected`` behavior event for the preference-memory pipeline.
``selected_item_id`` is a pure grounding channel on the execute-task input —
it must parse, with no routing side effect.
"""

from __future__ import annotations

import importlib
import sys

from fastapi.testclient import TestClient

from styleforge.models.task import TaskExecutionInput
from styleforge.repositories.database import database_session, initialize_database


def _import_api(db_dsn: str, monkeypatch) -> dict:
    monkeypatch.setenv("STYLEFORGE_DATABASE_DSN", db_dsn)
    sys.modules.pop("styleforge.api", None)
    api = importlib.import_module("styleforge.api")
    initialize_database(db_dsn)
    return {"api": api, "database_path": db_dsn}


def test_save_outfit_crud_over_http(db_dsn: str, monkeypatch) -> None:
    context = _import_api(db_dsn, monkeypatch)
    with TestClient(context["api"].app) as client:
        created = client.post(
            "/outfits/save",
            json={
                "user_id": "u",
                "outfit_id": "o1",
                "item_ids": ["shirt_a", "jeans_b"],
                "source_run_id": "run_1",
            },
        )
        assert created.status_code == 201
        assert created.json()["status"] == "saved"

        listed = client.get("/users/u/saved-outfits")
        assert listed.status_code == 200
        outfits = listed.json()["outfits"]
        assert len(outfits) == 1
        assert outfits[0]["outfit_id"] == "o1"
        assert outfits[0]["item_ids"] == ["shirt_a", "jeans_b"]
        assert outfits[0]["source_run_id"] == "run_1"

        # Upsert: re-saving the same outfit updates its snapshot, no new row.
        client.post(
            "/outfits/save",
            json={"user_id": "u", "outfit_id": "o1", "item_ids": ["hat_c"]},
        )
        assert len(client.get("/users/u/saved-outfits").json()["outfits"]) == 1

        deleted = client.delete("/users/u/saved-outfits/o1")
        assert deleted.status_code == 204
        assert client.get("/users/u/saved-outfits").json()["outfits"] == []


def test_save_outfit_requires_item_ids(db_dsn: str, monkeypatch) -> None:
    context = _import_api(db_dsn, monkeypatch)
    with TestClient(context["api"].app) as client:
        response = client.post(
            "/outfits/save",
            json={"user_id": "u", "outfit_id": "o1", "item_ids": []},
        )
        assert response.status_code == 422


def test_save_outfit_fires_outfit_selected_event(db_dsn: str, monkeypatch) -> None:
    context = _import_api(db_dsn, monkeypatch)
    with TestClient(context["api"].app) as client:
        client.post(
            "/outfits/save",
            json={"user_id": "u", "outfit_id": "o1", "item_ids": ["shirt_a"]},
        )
    with database_session(context["database_path"]) as connection:
        rows = connection.execute(
            "SELECT event_type, context_json FROM interaction_events WHERE user_id = %s",
            ("u",),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["event_type"] == "outfit_selected"
    assert "o1" in rows[0]["context_json"]


def test_task_execution_input_accepts_selected_item_id() -> None:
    parsed = TaskExecutionInput(
        user_id="u",
        request="上衣换一下",
        selected_item_id="shirt_a",
        current_outfit_id="o1",
    )
    assert parsed.selected_item_id == "shirt_a"
    assert parsed.current_outfit_id == "o1"
