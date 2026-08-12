from __future__ import annotations

import importlib
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from styleforge.repositories.catalog_repository import upsert_items
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.wardrobe_repository import add_items
from styleforge.workflow.task_workflow import MultiTaskWorkflow

from tests.extension_llm import ScriptedExtensionLlm, approved_review, intent_response
from tests.helpers import make_item


def _import_api(tmp_path: Path, monkeypatch) -> dict:
    database_path = tmp_path / "chat-api.db"
    monkeypatch.setenv("STYLEFORGE_DATABASE_PATH", str(database_path))
    sys.modules.pop("styleforge.api", None)
    api = importlib.import_module("styleforge.api")
    initialize_database(database_path)
    return {"api": api, "database_path": database_path}


def test_chat_sessions_crud_over_http(tmp_path: Path, monkeypatch) -> None:
    context = _import_api(tmp_path, monkeypatch)
    with TestClient(context["api"].app) as client:
        created = client.post("/users/u/chat-sessions", json={"title": ""})
        assert created.status_code == 201
        session = created.json()
        session_id = session["session_id"]
        assert session["title"].startswith("会话")

        listed = client.get("/users/u/chat-sessions")
        assert listed.status_code == 200
        assert listed.json()["count"] == 1

        detail = client.get(f"/chat-sessions/{session_id}", params={"user_id": "u"})
        assert detail.status_code == 200
        assert detail.json()["messages"] == []

        renamed = client.patch(
            f"/chat-sessions/{session_id}",
            params={"user_id": "u"},
            json={"title": "通勤搭配"},
        )
        assert renamed.status_code == 200
        assert renamed.json()["title"] == "通勤搭配"

        missing = client.get(
            "/chat-sessions/no-such", params={"user_id": "u"}
        )
        assert missing.status_code == 404

        deleted = client.delete(
            f"/chat-sessions/{session_id}", params={"user_id": "u"}
        )
        assert deleted.status_code == 200
        assert client.get("/users/u/chat-sessions").json()["count"] == 0


def test_execute_task_persists_user_and_assistant_messages(
    tmp_path: Path, monkeypatch
) -> None:
    context = _import_api(tmp_path, monkeypatch)
    database_path = context["database_path"]
    api = context["api"]
    items = [
        make_item("shirt", "top", "White shirt", "white"),
        make_item("jeans", "pants", "Blue jeans", "blue"),
        make_item("boots", "shoes", "Brown boots", "brown"),
    ]
    with database_session(database_path) as connection:
        upsert_items(connection, items, "test")
        add_items(connection, "u", [item.item_id for item in items])

    llm = ScriptedExtensionLlm(
        [
            intent_response("理解风格请求"),
            {
                "task_type": "style_advice",
                "status": "completed",
                "summary": "用已有衬衫落实风格",
                "result": {
                    "status": "completed",
                    "knowledge_type": "style",
                    "title": "American Vintage 美式复古",
                    "summary": "以衣橱基础单品建立复古层次。",
                    "principles": [
                        {"title": "层次", "content": "用基础单品控制复古元素数量。"}
                    ],
                    "wardrobe_matches": [{"item_id": "shirt", "name": "White shirt"}],
                    "evidence": [{"source_id": "style-american-vintage"}],
                    "limitations": [],
                },
                "used_item_ids": ["shirt"],
                "evidence_source_ids": ["style-american-vintage"],
            },
            approved_review(),
            # Successful execute also runs one memory-extraction call.
            {"memories": []},
        ]
    )
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=Path("knowledge"),
        llm_client=llm,
    )
    monkeypatch.setattr(api, "get_multi_task_workflow", lambda: workflow)

    with TestClient(api.app) as client:
        session = client.post("/users/u/chat-sessions", json={"title": ""}).json()
        session_id = session["session_id"]

        response = client.post(
            "/tasks/execute",
            json={
                "user_id": "u",
                "request": "American Vintage 风格应该怎么穿？",
                "requested_task_type": "style_advice",
                "session_id": session_id,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["session_id"] == session_id
        assert payload["message_id"]

        detail = client.get(f"/chat-sessions/{session_id}", params={"user_id": "u"})
        messages = detail.json()["messages"]
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[0]["content"] == "American Vintage 风格应该怎么穿？"
        assistant = messages[1]["result"]
        assert assistant["task_type"] == "style_advice"
        assert assistant["result"]["title"] == "American Vintage 美式复古"
        assert "outfit_context" in assistant


def test_execute_task_persists_auto_memory(tmp_path: Path, monkeypatch) -> None:
    context = _import_api(tmp_path, monkeypatch)
    database_path = context["database_path"]
    api = context["api"]
    items = [
        make_item("shirt", "top", "White shirt", "white"),
        make_item("jeans", "pants", "Blue jeans", "blue"),
        make_item("boots", "shoes", "Brown boots", "brown"),
    ]
    with database_session(database_path) as connection:
        upsert_items(connection, items, "test")
        add_items(connection, "u", [item.item_id for item in items])

    llm = ScriptedExtensionLlm(
        [
            intent_response("理解风格请求"),
            {
                "task_type": "style_advice",
                "status": "completed",
                "summary": "用已有衬衫落实风格",
                "result": {
                    "status": "completed",
                    "knowledge_type": "style",
                    "title": "American Vintage 美式复古",
                    "summary": "以衣橱基础单品建立复古层次。",
                    "principles": [
                        {"title": "层次", "content": "用基础单品控制复古元素数量。"}
                    ],
                    "wardrobe_matches": [{"item_id": "shirt", "name": "White shirt"}],
                    "evidence": [{"source_id": "style-american-vintage"}],
                    "limitations": [],
                },
                "used_item_ids": ["shirt"],
                "evidence_source_ids": ["style-american-vintage"],
            },
            approved_review(),
            # Successful execute also runs one memory-extraction call.
            {"memories": [{"category": "style", "content": "american vintage", "meta": {}}]},
        ]
    )
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=Path("knowledge"),
        llm_client=llm,
    )
    monkeypatch.setattr(api, "get_multi_task_workflow", lambda: workflow)

    with TestClient(api.app) as client:
        response = client.post(
            "/tasks/execute",
            json={
                "user_id": "u",
                "request": "American Vintage 风格应该怎么穿？",
                "requested_task_type": "style_advice",
            },
        )
        assert response.status_code == 200

        memories = client.get("/preferences/u/memories").json()["memories"]
        assert [(m["category"], m["content"], m["source"]) for m in memories] == [
            ("style", "american vintage", "auto")
        ]


def test_execute_task_with_unknown_session_is_404(tmp_path: Path, monkeypatch) -> None:
    context = _import_api(tmp_path, monkeypatch)
    with TestClient(context["api"].app) as client:
        response = client.post(
            "/tasks/execute",
            json={
                "user_id": "u",
                "request": "随便看看",
                "session_id": "no-such-session",
            },
        )
        assert response.status_code == 404


def test_execute_task_failure_still_records_failed_message(
    tmp_path: Path, monkeypatch
) -> None:
    context = _import_api(tmp_path, monkeypatch)
    database_path = context["database_path"]
    api = context["api"]
    # No scripted responses: the first LLM call raises, the task fails, and the
    # assistant failure is still persisted so the chain stays complete.
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=Path("knowledge"),
        llm_client=ScriptedExtensionLlm([]),
    )
    monkeypatch.setattr(api, "get_multi_task_workflow", lambda: workflow)

    with TestClient(api.app) as client:
        session = client.post("/users/u/chat-sessions", json={"title": ""}).json()
        session_id = session["session_id"]
        response = client.post(
            "/tasks/execute",
            json={
                "user_id": "u",
                "request": "American Vintage 风格应该怎么穿？",
                "requested_task_type": "style_advice",
                "session_id": session_id,
            },
        )
        assert response.status_code == 500

        detail = client.get(f"/chat-sessions/{session_id}", params={"user_id": "u"})
        messages = detail.json()["messages"]
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[1]["result"]["status"] == "failed"
