"""HTTP end-to-end for an extended task through the Multi-Agent Harness.

The extension task types now execute the primary Harness chain
(Coordinator → Extension subgraph → closing node), so the scripted model here
mirrors ``test_agentic_extension_primary``: coordinator decision, one READY turn,
a closing ``chat_json`` draft that satisfies the ``style_advice`` contract, and
the post-run memory extraction.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from styleforge.repositories.catalog_repository import upsert_items
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.wardrobe_repository import add_items
from styleforge.workflow.task_workflow import MultiTaskWorkflow

from tests.helpers import make_item
from tests.llm.fake_llm import FakeLlm


def test_execute_and_read_extended_task_over_http(
    db_dsn: str,
    monkeypatch,
) -> None:
    database_path = db_dsn
    monkeypatch.setenv("STYLEFORGE_DATABASE_DSN", database_path)
    # This test verifies the MCP-backed weather health contract, so it must not
    # inherit service-disable flags from the surrounding CI environment.
    monkeypatch.setenv("STYLEFORGE_WEATHER_ENABLED", "true")
    monkeypatch.setenv("STYLEFORGE_MCP_ENABLED", "true")
    monkeypatch.setenv("STYLEFORGE_MCP_WEATHER_ENABLED", "true")
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

    llm = FakeLlm(
        [
            {
                "user_intent": {
                    "message": "American Vintage 风格应该怎么穿？",
                    "goal": "获得 American Vintage 风格建议",
                    "requirements": [],
                },
                "status": "completed",
                "summary": "用已有衬衫和牛仔裤落实风格",
                "result": {
                    "status": "completed",
                    "knowledge_type": "style",
                    "title": "American Vintage 美式复古",
                    "summary": "以衣橱基础单品建立复古层次。",
                    "principles": [
                        {
                            "title": "层次",
                            "section": "风格建议",
                            "content": "用基础单品控制复古元素数量。",
                            "description": "以衬衫叠穿或内搭形式建立层次。",
                        }
                    ],
                    "wardrobe_matches": [{"item_id": "shirt", "name": "White shirt"}],
                    "evidence": [{"source_id": "style-american-vintage"}],
                    "limitations": [],
                },
            },
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
        assert health.json()["outfit_recommend_result_schema"] == (
            "styleforge.outfit-recommend-result.v1"
        )
        assert health.json()["api_started_at"]
        assert health.json()["weather"]["provider"] == "mcp-fetch+open-meteo"
        assert health.json()["weather"]["enabled"] is True
        assert health.json()["weather"]["mcp_primary"] is True
        assert health.json()["mcp"]["server"]["endpoint"] == "/mcp/"
        assert len(health.json()["mcp"]["server"]["tools"]) == 5
        assert {
            "official-fetch",
            "official-time",
        }.issubset(
            {server["name"] for server in health.json()["mcp"]["client"]["servers"]}
        )
        assert health.json()["database"] == {
            "backend": "postgresql",
            "status": "connected",
        }
        retrieval_health = health.json()["wardrobe_retrieval"]
        assert retrieval_health["strategy"] == (
            "fashionclip_hybrid_with_keyword_fallback"
        )
        assert "semantic_artifacts_available" in retrieval_health
        assert "model_loaded" in retrieval_health
        assert database_path not in str(health.json())

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
        assert payload["status"] == "completed"
        assert payload["selected_subgraph"] == "agentic_harness"
        assert payload["result"]["evidence"]
        # Complete deterministic facts route directly to one semantic closing call.
        assert payload["llm_call_count"] == 1
        assert payload["semantic_intent"]["goal"] == "获得 American Vintage 风格建议"

        stored = client.get(f"/tasks/api-user/{payload['run_id']}")
        assert stored.status_code == 200
        assert stored.json()["result"]["title"] == payload["result"]["title"]
        assert stored.json()["diagnostics"] == payload["diagnostics"]

        missing = client.get(f"/tasks/other-user/{payload['run_id']}")
        assert missing.status_code == 404
