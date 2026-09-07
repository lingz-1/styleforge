"""HTTP tests for the batch photo-recognition background workflow.

The batch endpoints run on a thread pool, so tests poll ``GET
.../recognition-batches/{batch_id}`` until the batch reaches a terminal state
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

    def add_response(self, response: dict[str, Any] | Exception) -> None:
        with self._lock:
            self._responses.append(response)


def _photo_bytes() -> bytes:
    image = Image.new("RGB", (32, 32), (40, 60, 120))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _import_api(db_dsn, monkeypatch) -> Any:
    monkeypatch.setenv("STYLEFORGE_DATABASE_DSN", db_dsn)
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


def _wait_terminal(
    client: TestClient, user_id: str, batch_id: str, timeout: float = 10.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(
            f"/wardrobes/{user_id}/recognition-batches/{batch_id}"
        )
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {
            "completed",
            "partial_failed",
            "interrupted",
            "cancelled",
        }:
            return payload
        time.sleep(0.05)
    pytest.fail("batch did not complete in time")


def test_batch_auto_adds_reliable_and_flags_low_confidence(
    db_dsn, monkeypatch
) -> None:
    api = _import_api(db_dsn, monkeypatch)
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
                "auto_embed": False,
            },
        )

    assert response.status_code == 202
    payload = response.json()
    assert payload["total"] == 3
    assert payload["status"] in {"accepted", "recognizing"}
    batch_id = payload["batch_id"]

    with TestClient(api.app) as client:
        final = _wait_terminal(client, "u1", batch_id)

    assert final["status"] == "partial_failed"
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
    assert len(pending) == 2  # auto_embed=False leaves the explicit pending state


def test_batch_provider_failure_triggers_circuit_break(db_dsn, monkeypatch) -> None:
    api = _import_api(db_dsn, monkeypatch)
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
        final = _wait_terminal(client, "u1", response.json()["batch_id"])

    assert final["status"] == "partial_failed"
    assert final["succeeded"] == 0
    assert final["failed"] == 5
    reasons = {r["reason"] for r in final["results"]}
    # Two real failures trip the breaker; the rest are short-circuited.
    assert reasons == {"vision_unavailable", "provider_unavailable"}


def test_batch_create_failure_is_reported(db_dsn, monkeypatch) -> None:
    api = _import_api(db_dsn, monkeypatch)
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
                "auto_embed": False,
            },
        )
    assert response.status_code == 202

    with TestClient(api.app) as client:
        final = _wait_terminal(client, "u1", response.json()["batch_id"])

    assert final["status"] == "partial_failed"
    assert final["failed"] == 1
    assert final["results"][0]["reason"] == "create_failed"


def test_batch_validation_rules(db_dsn, monkeypatch) -> None:
    api = _import_api(db_dsn, monkeypatch)
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


def test_batch_ownership_enforced(db_dsn, monkeypatch) -> None:
    api = _import_api(db_dsn, monkeypatch)
    fake = ThreadSafeFakeVision([_reliable_response()])
    monkeypatch.setattr(api, "vision_client_from_settings", lambda _settings: fake)

    with TestClient(api.app) as client:
        response = client.post(
            "/wardrobes/u1/items/batch-recognize",
            json={
                "images": [
                    {"filename": "t.png", "content_base64": _b64(_photo_bytes())}
                ],
                "auto_embed": False,
            },
        )
    batch_id = response.json()["batch_id"]

    with TestClient(api.app) as client:
        other = client.get(f"/wardrobes/u2/recognition-batches/{batch_id}")
    assert other.status_code == 404

    with TestClient(api.app) as client:
        own = client.get(f"/wardrobes/u1/recognition-batches/{batch_id}")
        _wait_terminal(client, "u1", batch_id)
    assert own.status_code == 200


def test_batch_auto_embeds_once_and_failed_embedding_can_retry(
    db_dsn, monkeypatch
) -> None:
    api = _import_api(db_dsn, monkeypatch)
    fake = ThreadSafeFakeVision([_reliable_response(), _reliable_response()])
    monkeypatch.setattr(api, "vision_client_from_settings", lambda _settings: fake)

    import styleforge.services.recognition_batch as recognition_batch

    calls: list[list[str]] = []

    def _embed(**kwargs: Any) -> dict[str, Any]:
        item_ids = list(kwargs["item_ids"])
        calls.append(item_ids)
        if len(calls) == 1:
            raise RuntimeError("temporary encoder failure")
        from styleforge.repositories.database import database_session

        with database_session(db_dsn) as connection:
            connection.execute(
                "UPDATE catalog_items SET embedding_status = 'ready' "
                "WHERE item_id = ANY(%s)",
                (item_ids,),
            )
        return {
            "status": "completed",
            "requested_items": len(item_ids),
            "embedded_items": len(item_ids),
            "image_embeddings": len(item_ids),
            "text_embeddings": 0,
        }

    monkeypatch.setattr(recognition_batch, "embed_personal_items", _embed)

    with TestClient(api.app) as client:
        response = client.post(
            "/wardrobes/u1/items/batch-recognize",
            json={
                "images": [
                    {"filename": f"{index}.png", "content_base64": _b64(_photo_bytes())}
                    for index in range(2)
                ],
            },
        )
        assert response.status_code == 202
        batch_id = response.json()["batch_id"]
        first = _wait_terminal(client, "u1", batch_id)
        assert first["embedding"]["status"] == "failed"
        assert first["embedding"]["retryable"] is True
        assert first["embedding"]["wardrobe_commit_preserved"] is True

        # Simulate a late worker callback after the first embedding attempt failed.
        from styleforge.repositories import recognition_batch_repository as batches
        from styleforge.repositories.database import database_session

        with database_session(db_dsn) as connection:
            refreshed, should_embed = batches.refresh_progress(
                connection,
                batch_id=batch_id,
                wall_ms=0,
            )
        assert should_embed is False
        assert refreshed is not None
        assert refreshed["embedding"]["status"] == "failed"

        retry = client.post(
            f"/wardrobes/u1/recognition-batches/{batch_id}/retry-embedding"
        )
        assert retry.status_code == 202
        assert retry.json()["status"] == "embedding"
        final = _wait_terminal(client, "u1", batch_id)

        wardrobe = client.get("/wardrobes/u1")

    assert len(calls) == 2
    assert len(calls[0]) == 2
    assert set(calls[0]) == set(calls[1])
    assert final["embedding"]["status"] == "completed"
    assert final["embedding"]["embedded_items"] == 2
    ready = [item for item in wardrobe.json()["items"] if item["embedding_status"] == "ready"]
    assert len(ready) == 2


def test_active_embedding_batch_cannot_be_deleted(db_dsn, monkeypatch) -> None:
    api = _import_api(db_dsn, monkeypatch)
    fake = ThreadSafeFakeVision([_reliable_response()])
    monkeypatch.setattr(api, "vision_client_from_settings", lambda _settings: fake)

    import styleforge.services.recognition_batch as recognition_batch

    started = threading.Event()
    release = threading.Event()

    def _slow_embed(**kwargs: Any) -> dict[str, Any]:
        started.set()
        assert release.wait(timeout=5)
        return {
            "status": "completed",
            "requested_items": len(kwargs["item_ids"]),
            "embedded_items": len(kwargs["item_ids"]),
            "image_embeddings": len(kwargs["item_ids"]),
            "text_embeddings": 0,
        }

    monkeypatch.setattr(recognition_batch, "embed_personal_items", _slow_embed)

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
        assert started.wait(timeout=5)

        active = client.get(f"/wardrobes/u1/recognition-batches/{batch_id}")
        assert active.json()["status"] == "embedding"
        blocked = client.delete(f"/wardrobes/u1/recognition-batches/{batch_id}")
        assert blocked.status_code == 409

        release.set()
        final = _wait_terminal(client, "u1", batch_id)
        assert final["embedding"]["status"] == "completed"


def test_user_can_retry_pending_embeddings_after_batch_state_is_gone(
    tmp_path, db_dsn, monkeypatch
) -> None:
    api = _import_api(db_dsn, monkeypatch)
    from styleforge.services.wardrobe_item_service import create_photo_item

    def _create(user_id: str) -> str:
        created = create_photo_item(
            database_path=db_dsn,
            artifact_root=tmp_path / "artifacts",
            user_id=user_id,
            image_bytes=_photo_bytes(),
            model_dir=tmp_path / "models",
            name="T恤",
            item_type="top",
            color="black",
            skip_embedding=True,
        )
        return created["item_id"]

    own_item = _create("u1")
    other_item = _create("u2")
    calls: list[list[str]] = []

    def _embed(**kwargs: Any) -> dict[str, Any]:
        item_ids = list(kwargs["item_ids"])
        calls.append(item_ids)
        from styleforge.repositories.database import database_session

        with database_session(db_dsn) as connection:
            connection.execute(
                "UPDATE catalog_items SET embedding_status = 'ready' "
                "WHERE item_id = ANY(%s)",
                (item_ids,),
            )
        return {"status": "completed", "embedded_items": len(item_ids)}

    monkeypatch.setattr(api, "embed_personal_items", _embed)
    with TestClient(api.app) as client:
        response = client.post(
            "/wardrobes/u1/embeddings/retry",
            json={"item_ids": [own_item, other_item]},
        )
        second = client.post("/wardrobes/u1/embeddings/retry", json={})
        own = client.get("/wardrobes/u1").json()["items"]
        other = client.get("/wardrobes/u2").json()["items"]

    assert response.status_code == 200
    assert response.json()["item_ids"] == [own_item]
    assert response.json()["embedded_items"] == 1
    assert calls == [[own_item]]
    assert second.status_code == 200
    assert second.json()["eligible_items"] == 0
    assert own[0]["embedding_status"] == "ready"
    assert other[0]["embedding_status"] == "pending"


def test_personal_embedding_retry_returns_safe_retryable_error(
    tmp_path, db_dsn, monkeypatch
) -> None:
    api = _import_api(db_dsn, monkeypatch)
    from styleforge.services.wardrobe_item_service import create_photo_item

    created = create_photo_item(
        database_path=db_dsn,
        artifact_root=tmp_path / "artifacts",
        user_id="u1",
        image_bytes=_photo_bytes(),
        model_dir=tmp_path / "models",
        name="T恤",
        item_type="top",
        skip_embedding=True,
    )

    def _fail(**_kwargs: Any) -> None:
        raise RuntimeError("secret local CUDA path")

    monkeypatch.setattr(api, "embed_personal_items", _fail)
    with TestClient(api.app, raise_server_exceptions=False) as client:
        response = client.post(
            "/wardrobes/u1/embeddings/retry",
            json={"item_ids": [created["item_id"]]},
        )

    assert response.status_code == 503
    assert "secret local CUDA path" not in response.text
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert response.json()["error"]["retryable"] is True
    assert response.json()["error"]["details"] == {"eligible_items": 1}


def test_create_photo_item_skip_embedding(tmp_path, db_dsn, monkeypatch) -> None:
    monkeypatch.setenv("STYLEFORGE_DATABASE_DSN", db_dsn)
    from styleforge.services.wardrobe_item_service import create_photo_item

    created = create_photo_item(
        database_path=db_dsn,
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


def test_staged_input_is_user_scoped_and_removed_with_batch(
    db_dsn, monkeypatch
) -> None:
    api = _import_api(db_dsn, monkeypatch)
    fake = ThreadSafeFakeVision([_reliable_response()])
    monkeypatch.setattr(api, "vision_client_from_settings", lambda _settings: fake)

    with TestClient(api.app) as client:
        response = client.post(
            "/wardrobes/u1/items/batch-recognize",
            json={
                "images": [
                    {"filename": "owned.png", "content_base64": _b64(_photo_bytes())}
                ],
                "auto_embed": False,
            },
        )
        batch_id = response.json()["batch_id"]
        final = _wait_terminal(client, "u1", batch_id)
        own = client.get(
            f"/wardrobes/u1/recognition-batches/{batch_id}/items/0/image"
        )
        other = client.get(
            f"/wardrobes/u2/recognition-batches/{batch_id}/items/0/image"
        )

        from styleforge.services.recognition_batch import batch_storage_root

        staged_root = batch_storage_root(api.settings.artifact_root, batch_id)
        assert staged_root.is_dir()
        deleted = client.delete(f"/wardrobes/u1/recognition-batches/{batch_id}")

    assert final["status"] == "completed"
    assert own.status_code == 200
    assert own.content == _photo_bytes()
    assert other.status_code == 404
    assert deleted.status_code == 200
    assert not staged_root.exists()


def test_retry_recognition_only_reprocesses_failed_items(db_dsn, monkeypatch) -> None:
    api = _import_api(db_dsn, monkeypatch)
    fake = ThreadSafeFakeVision(
        [_reliable_response(), {"type": "shirt", "confidence": 0.05}]
    )
    monkeypatch.setattr(api, "vision_client_from_settings", lambda _settings: fake)

    with TestClient(api.app) as client:
        response = client.post(
            "/wardrobes/u1/items/batch-recognize",
            json={
                "images": [
                    {"filename": f"{index}.png", "content_base64": _b64(_photo_bytes())}
                    for index in range(2)
                ],
                "auto_embed": False,
            },
        )
        batch_id = response.json()["batch_id"]
        first = _wait_terminal(client, "u1", batch_id)
        first_saved_id = next(
            result["item_id"] for result in first["results"] if result["item_id"]
        )

        fake.add_response(_reliable_response())
        retry = client.post(f"/wardrobes/u1/recognition-batches/{batch_id}/retry")
        final = _wait_terminal(client, "u1", batch_id)
        wardrobe = client.get("/wardrobes/u1").json()["items"]

    assert first["status"] == "partial_failed"
    assert retry.status_code == 202
    assert final["status"] == "completed"
    assert final["succeeded"] == 2
    assert len(wardrobe) == 2
    assert first_saved_id in {item["item_id"] for item in wardrobe}
    assert sorted(result["attempt_count"] for result in final["results"]) == [1, 2]


def test_restart_recovery_is_idempotent_after_item_was_saved(
    db_dsn, monkeypatch
) -> None:
    api = _import_api(db_dsn, monkeypatch)
    first_fake = ThreadSafeFakeVision([_reliable_response()])
    monkeypatch.setattr(
        api, "vision_client_from_settings", lambda _settings: first_fake
    )

    with TestClient(api.app) as client:
        response = client.post(
            "/wardrobes/u1/items/batch-recognize",
            json={
                "images": [
                    {"filename": "resume.png", "content_base64": _b64(_photo_bytes())}
                ],
                "auto_embed": False,
            },
        )
        batch_id = response.json()["batch_id"]
        first = _wait_terminal(client, "u1", batch_id)

    # Simulate a process crash after deterministic item creation but before its
    # batch item state was durably marked succeeded.
    from styleforge.repositories.database import database_session

    with database_session(db_dsn) as connection:
        connection.execute(
            "UPDATE recognition_batch_items SET status = 'recognizing' "
            "WHERE batch_id = %s",
            (batch_id,),
        )
        connection.execute(
            "UPDATE recognition_batches SET status = 'recognizing', done = 0, "
            "succeeded = 0, failed = 0, finished_at = NULL WHERE batch_id = %s",
            (batch_id,),
        )

    from styleforge.services.recognition_batch import resume_incomplete_batches

    resumed = resume_incomplete_batches(
        vision_client=ThreadSafeFakeVision([_reliable_response()]),
        database_path=db_dsn,
        artifact_root=api.settings.artifact_root,
        model_dir=api.settings.artifact_root / "models",
    )
    with TestClient(api.app) as client:
        final = _wait_terminal(client, "u1", batch_id)
        wardrobe = client.get("/wardrobes/u1").json()["items"]

    assert resumed == {"recoverable": 1, "resumed": 1, "interrupted": 0}
    assert final["status"] == "completed"
    assert len(wardrobe) == 1
    assert wardrobe[0]["item_id"] == first["results"][0]["item_id"]
    assert final["results"][0]["attempt_count"] == 2
