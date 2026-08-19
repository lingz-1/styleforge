# -*- coding: utf-8 -*-
"""H3a-2 api.py write point (缺口 5): thread views update the moment the user
message is accepted — before execution — and the outfit-cache warm never drops
them for the next turn.

The two frozen behaviours under test:
  1. ``execute_task`` passes a ``session_context`` carrying the updated
     ``thread_preferences`` / ``thread_grounding`` into the workflow.
  2. ``_append_chat_success`` merges the pre-run thread views back into the
     outfit cache instead of overwriting them.
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


def test_execute_task_updates_thread_views_before_run(db_dsn, monkeypatch) -> None:
    context = _import_api(db_dsn, monkeypatch)
    api = context["api"]
    captured: dict = {}

    class StubWorkflow:
        def execute(self, request, *, session_context=None):
            captured["session_context"] = session_context
            return {
                "run_id": "r1",
                "user_id": request.user_id,
                "request": request.request,
                "task_type": "style_advice",
                "status": "completed",
                "result": {"status": "completed", "summary": "ok"},
            }

    monkeypatch.setattr(api, "get_multi_task_workflow", lambda: StubWorkflow())

    with TestClient(api.app) as client:
        session = client.post("/users/u/chat-sessions", json={"title": ""}).json()
        response = client.post(
            "/tasks/execute",
            json={
                "user_id": "u",
                "request": "这次想穿黑一点，帮我搭一套",
                "session_id": session["session_id"],
            },
        )
        assert response.status_code == 200

    sc = captured["session_context"]
    assert sc is not None
    prefs = sc["thread_preferences"]
    assert [item["value"] for item in prefs["preferences"]] == ["black"]
    assert prefs["preferences"][0]["polarity"] == "positive"
    assert sc["thread_grounding"]["updated_at"] is not None


def test_append_chat_success_preserves_thread_views(db_dsn, monkeypatch) -> None:
    """The outfit-cache warm must never overwrite the pre-run thread views."""
    context = _import_api(db_dsn, monkeypatch)
    api = context["api"]
    # Simulate a Redis that already holds the pre-run thread views.
    pre_run = {
        "current_outfit_id": "",
        "current_item_ids": [],
        "thread_preferences": {
            "preferences": [
                {"attribute": "color", "value": "black", "polarity": "positive", "source_turn": "想穿黑一点"}
            ],
            "constraints": [],
            "style_adjustments": [],
            "updated_at": "2026-08-19T00:00:00+00:00",
        },
        "thread_grounding": {"updated_at": "2026-08-19T00:00:00+00:00"},
    }
    monkeypatch.setattr(api, "get_session_outfit_cache", lambda client, session_id: pre_run)
    written: dict = {}
    monkeypatch.setattr(
        api, "cache_session_outfit", lambda client, session_id, ctx, ttl: written.update(ctx)
    )

    payload = {
        "run_id": "r2",
        "task_type": "outfit_recommend",
        "result": {
            "structured_result": {
                "recommendations": [{"outfit_id": "o1", "item_ids": ["top-1", "bottom-1"]}]
            }
        },
    }
    from styleforge.repositories.chat_repository import create_chat_session

    # Commit the session row before _append_chat_success opens its own connection
    # (its chat_messages INSERT is FK-checked against chat_sessions).
    with database_session(context["database_path"]) as connection:
        session_id = create_chat_session(connection, user_id="u", title="")["session_id"]
    request = TaskExecutionInput(
        user_id="u", session_id=session_id, request="这次想穿黑一点，帮我搭一套"
    )
    with database_session(context["database_path"]):
        api._append_chat_success(request, payload)

    assert written["current_item_ids"] == ["top-1", "bottom-1"]
    assert written["thread_preferences"]["preferences"][0]["value"] == "black"
    assert "updated_at" in written["thread_grounding"]
