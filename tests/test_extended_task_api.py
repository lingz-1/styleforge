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


def test_execute_and_read_extended_task_over_http(
    db_dsn: str,
    monkeypatch,
) -> None:
    database_path = db_dsn
    monkeypatch.setenv("STYLEFORGE_DATABASE_DSN", database_path)
    sys.modules.pop("styleforge.api", None)
    api = importlib.import_module("styleforge.api")

    initialize_database(database_path)
    items = [
        make_item("shirt", "top", "White shirt", "white"),
        make_item("jeans", "pants", "Blue jeans", "blue"),
        make_item("boots", "shoes", "Brown ankle boots", "brown"),
    ]
    with database_session(database_path) as connection:
        upsert_items(connection, items, "test")
        add_items(connection, "api-user", [item.item_id for item in items])

    llm = ScriptedExtensionLlm(
        [
            intent_response("理解美式复古风格请求"),
            {
                "task_type": "style_advice",
                "status": "completed",
                "summary": "用已有衬衫和牛仔裤落实风格",
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
            {"evidence": []},
        ]
    )
    workflow = MultiTaskWorkflow(
        database_path=database_path,
        knowledge_root=Path("knowledge"),
        llm_client=llm,
    )
    monkeypatch.setattr(api, "get_multi_task_workflow", lambda: workflow)

    with TestClient(api.app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["extension_prompt_version"] == "extension-three-agent-v3.2"
        assert health.json()["api_started_at"]
        assert health.json()["weather"]["provider"] == "open-meteo"
        assert health.json()["weather"]["enabled"] is True

        response = client.post(
            "/tasks/execute",
            json={
                "user_id": "api-user",
                "request": "American Vintage 风格应该怎么穿？",
                "requested_task_type": "style_advice",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["task_type"] == "style_advice"
        assert payload["result"]["evidence"]
        assert payload["llm_call_count"] == 3

        stored = client.get(f"/tasks/api-user/{payload['run_id']}")
        assert stored.status_code == 200
        assert stored.json()["result"]["title"] == payload["result"]["title"]

        missing = client.get(f"/tasks/other-user/{payload['run_id']}")
        assert missing.status_code == 404
