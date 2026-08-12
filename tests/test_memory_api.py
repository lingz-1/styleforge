from __future__ import annotations

import importlib
import sys

from fastapi.testclient import TestClient


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("STYLEFORGE_DATABASE_PATH", str(tmp_path / "memory-api.db"))
    sys.modules.pop("styleforge.api", None)
    api = importlib.import_module("styleforge.api")
    return TestClient(api.app)


def test_memory_crud_over_http(tmp_path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        created = client.post(
            "/preferences/u/memories",
            json={"category": "color", "content": "黑色", "confidence": 0.9},
        )
        assert created.status_code == 201
        memory = created.json()
        assert memory["source"] == "manual"
        assert memory["content"] == "黑色"

        listed = client.get("/preferences/u/memories")
        assert listed.status_code == 200
        assert listed.json()["count"] == 1
        assert listed.json()["memories"][0]["memory_id"] == memory["memory_id"]

        updated = client.patch(
            f"/preferences/u/memories/{memory['memory_id']}",
            json={"confidence": 0.7},
        )
        assert updated.status_code == 200
        assert updated.json()["confidence"] == 0.7

        forgotten = client.delete(f"/preferences/u/memories/{memory['memory_id']}")
        assert forgotten.status_code == 200
        assert client.get("/preferences/u/memories").json()["count"] == 0


def test_memory_api_validates_and_checks_ownership(tmp_path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        bad_category = client.post(
            "/preferences/u/memories",
            json={"category": "nope", "content": "x"},
        )
        assert bad_category.status_code == 422

        bad_confidence = client.post(
            "/preferences/u/memories",
            json={"category": "color", "content": "黑色", "confidence": 2.0},
        )
        assert bad_confidence.status_code == 422

        missing = client.delete("/preferences/u/memories/99999")
        assert missing.status_code == 404

        missing_patch = client.patch(
            "/preferences/u/memories/99999", json={"confidence": 0.5}
        )
        assert missing_patch.status_code == 404
