"""HTTP tests for the batch photo-recognition background workflow.

The batch endpoints run on a thread pool, so tests poll ``GET
.../recognition-batches/{batch_id}`` until the batch reaches ``completed``
before asserting on the database. A thread-safe fake vision client returns
preset responses in order so concurrent workers behave deterministically.
"""

from __future__ import annotations

import base64
import importlib
import io
import sys
import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from styleforge.vision.vision_client import VisionUnavailable


class ThreadSafeFakeVision:
    """Returns preset responses (or raises preset errors) in order."""

    name = "vision.analyze_clothing"

    def __init__(self, responses: list[dict[str, Any] | Exception]) -> None:
        self._responses = list(responses)
        self._lock = threading.Lock()

    def analyze_image(self, image_bytes: bytes, prompt: str) -> dict[str, Any]:
        with self._lock:
            item = self._responses.pop(0) if self._responses else {}
        if isinstance(item, Exception):
            raise item
        return item


def _photo_bytes() -> bytes:
    image = Image.new("RGB", (32, 32), (40, 60, 120))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _import_api(tmp_path, monkeypatch) -> Any:
    monkeypatch.setenv("STYLEFORGE_DATABASE_PATH", str(tmp_path / "batch.db"))
    monkeypatch.delenv("STYLEFORGE_DEFAULT_LOCATION", raising=False)
    sys.modules.pop("styleforge.api", None)
    return importlib.import_module("styleforge.api")


def _reliable_response() -> dict[str, Any]:
    return {
        "type": "t-shirt",
        "primary_color": "black",
        "colors": ["black"],
        "pattern": "solid",
        "material": "cotton",
        "formality": "casual",
        "style": ["streetwear"],
        "season": ["summer"],
        "description": "黑色纯棉短袖T恤",
        "confidence": 0.92,
    }


def _wait_completed(
    client: TestClient, user_id: str, batch_id: str, timeout: float = 10.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(
            f"/wardrobes/{user_id}/recognition-batches/{batch_id}"
        )
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] != "running":
            return payload
        time.sleep(0.05)
    pytest.fail("batch did not complete in time")


def test_batch_auto_adds_reliable_and_flags_low_confidence(
    tmp_path, monkeypatch
) -> None:
    api = _import_api(tmp_path, monkeypatch)
    fake = ThreadSafeFakeVision(
        [_reliable_response(), _reliable_response(), {"type": "shirt", "confidence": 0.05}]
    )
    monkeypatch.setattr(api, "vision_client_from_settings", lambda _settings: fake)

    with TestClient(api.app) as client:
        response = client.post(
            "/wardrobes/u1/items/batch-recognize",
            json={
                "images": [
                    {"filename": f"{index}.png", "content_base64": _b64(_photo_bytes())}
                    for index in range(3)
                ],
                "default_gender": "women",
            },
        )

    assert response.status_code == 202
    payload = response.json()
    assert payload["total"] == 3
    assert payload["status"] == "running"
    batch_id = payload["batch_id"]

    with TestClient(api.app) as client:
        final = _wait_completed(client, "u1", batch_id)

    assert final["status"] == "completed"
    assert final["succeeded"] == 2
    assert final["failed"] == 1
    failures = [r for r in final["results"] if r["status"] == "failed"]
    assert len(failures) == 1
    assert failures[0]["reason"] == "low_confidence"
    assert failures[0]["attributes"] is not None  # kept for manual add

    with TestClient(api.app) as client:
        wardrobe = client.get("/wardrobes/u1")
    assert wardrobe.status_code == 200
    pending = [i for i in wardrobe.json()["items"] if i["embedding_status"] == "pending"]
    assert len(pending) == 2  # skip_embedding left them pending, none on GPU


def test_batch_provider_failure_triggers_circuit_break(tmp_path, monkeypatch) -> None:
    api = _import_api(tmp_path, monkeypatch)
    fake = ThreadSafeFakeVision([VisionUnavailable("proxy down")] * 5)
    monkeypatch.setattr(api, "vision_client_from_settings", lambda _settings: fake)

    with TestClient(api.app) as client:
        response = client.post(
            "/wardrobes/u1/items/batch-recognize",
            json={
                "images": [
                    {"filename": f"{index}.png", "content_base64": _b64(_photo_bytes())}
                    for index in range(5)
                ],
            },
        )
    assert response.status_code == 202

    with TestClient(api.app) as client:
        final = _wait_completed(client, "u1", response.json()["batch_id"])

    assert final["status"] == "completed"
    assert final["succeeded"] == 0
    assert final["failed"] == 5
    reasons = {r["reason"] for r in final["results"]}
    # Two real failures trip the breaker; the rest are short-circuited.
    assert reasons == {"vision_unavailable", "provider_unavailable"}


def test_batch_create_failure_is_reported(tmp_path, monkeypatch) -> None:
    api = _import_api(tmp_path, monkeypatch)
    fake = ThreadSafeFakeVision([_reliable_response()])
    monkeypatch.setattr(api, "vision_client_from_settings", lambda _settings: fake)

    import styleforge.services.recognition_batch as recognition_batch

    def _boom(**kwargs: Any) -> None:
        raise ValueError("bad item type")

    monkeypatch.setattr(recognition_batch, "create_photo_item", _boom)

    with TestClient(api.app) as client:
        response = client.post(
            "/wardrobes/u1/items/batch-recognize",
            json={
                "images": [
                    {"filename": "t.png", "content_base64": _b64(_photo_bytes())}
                ],
            },
        )
    assert response.status_code == 202

    with TestClient(api.app) as client:
        final = _wait_completed(client, "u1", response.json()["batch_id"])

    assert final["status"] == "completed"
    assert final["failed"] == 1
    assert final["results"][0]["reason"] == "create_failed"


def test_batch_validation_rules(tmp_path, monkeypatch) -> None:
    api = _import_api(tmp_path, monkeypatch)
    fake = ThreadSafeFakeVision([{}])
    monkeypatch.setattr(api, "vision_client_from_settings", lambda _settings: fake)
    one = {"filename": "t.png", "content_base64": _b64(_photo_bytes())}

    with TestClient(api.app) as client:
        too_many = client.post(
            "/wardrobes/u1/items/batch-recognize",
            json={"images": [one] * 31},
        )
    assert too_many.status_code == 422

    big = {"filename": "b.png", "content_base64": _b64(b"x" * (17 * 1024 * 1024))}
    with TestClient(api.app) as client:
        too_big = client.post(
            "/wardrobes/u1/items/batch-recognize",
            json={"images": [big] * 4},
        )
    assert too_big.status_code == 413

    with TestClient(api.app) as client:
        bad_base64 = client.post(
            "/wardrobes/u1/items/batch-recognize",
            json={"images": [{"filename": "t.png", "content_base64": "!!!"}]},
        )
    assert bad_base64.status_code == 422

    monkeypatch.setattr(api, "vision_client_from_settings", lambda _settings: None)
    with TestClient(api.app) as client:
        disabled = client.post(
            "/wardrobes/u1/items/batch-recognize",
            json={"images": [one]},
        )
    assert disabled.status_code == 503


def test_batch_ownership_enforced(tmp_path, monkeypatch) -> None:
    api = _import_api(tmp_path, monkeypatch)
    fake = ThreadSafeFakeVision([_reliable_response()])
    monkeypatch.setattr(api, "vision_client_from_settings", lambda _settings: fake)

    with TestClient(api.app) as client:
        response = client.post(
            "/wardrobes/u1/items/batch-recognize",
            json={
                "images": [
                    {"filename": "t.png", "content_base64": _b64(_photo_bytes())}
                ],
            },
        )
    batch_id = response.json()["batch_id"]

    with TestClient(api.app) as client:
        other = client.get(f"/wardrobes/u2/recognition-batches/{batch_id}")
    assert other.status_code == 404

    with TestClient(api.app) as client:
        own = client.get(f"/wardrobes/u1/recognition-batches/{batch_id}")
    assert own.status_code == 200


def test_create_photo_item_skip_embedding(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("STYLEFORGE_DATABASE_PATH", str(tmp_path / "skip.db"))
    from styleforge.services.wardrobe_item_service import create_photo_item

    created = create_photo_item(
        database_path=tmp_path / "skip.db",
        artifact_root=tmp_path / "artifacts",
        user_id="u1",
        image_bytes=_photo_bytes(),
        model_dir=tmp_path / "models",
        device="cuda",
        name="T恤",
        item_type="top",
        subtype="t_shirt",
        color="black",
        skip_embedding=True,
    )
    assert created["embedding"] == {"status": "skipped"}
    assert created["item"]["embedding_status"] == "pending"
