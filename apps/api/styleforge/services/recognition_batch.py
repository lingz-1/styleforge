"""Persistent, resumable background batches for wardrobe photo recognition."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Protocol

from styleforge.repositories import recognition_batch_repository as batches
from styleforge.repositories.database import database_session, initialize_database
from styleforge.services.personal_embeddings import embed_personal_items
from styleforge.services.wardrobe_item_service import create_photo_item
from styleforge.vision.clothing_analysis import (
    attributes_to_dict,
    build_analysis_prompt,
    is_reliable_analysis,
    map_ai_type_to_item_fields,
    parse_attributes,
)
from styleforge.vision.vision_client import VisionInvalidJson, VisionUnavailable


CONCURRENCY = 3
PROVIDER_CIRCUIT_BREAK_LIMIT = 2
ITEM_NAMESPACE = uuid.UUID("29d959d2-377c-4a45-a275-3fa29c556fd1")
logger = logging.getLogger(__name__)

REASON_LOW_CONFIDENCE = "low_confidence"
REASON_VISION_UNAVAILABLE = "vision_unavailable"
REASON_INVALID_JSON = "invalid_json"
REASON_INVALID_IMAGE = "invalid_image"
REASON_CREATE_FAILED = "create_failed"
REASON_PROVIDER_UNAVAILABLE = "provider_unavailable"
REASON_INTERNAL_ERROR = "internal_error"


class VisionClientLike(Protocol):
    def analyze_image(self, image_bytes: bytes, prompt: str) -> dict[str, Any]: ...


_recognition_executor = ThreadPoolExecutor(
    max_workers=CONCURRENCY,
    thread_name_prefix="vision-batch",
)
_embedding_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="embedding-batch")
_runtime_lock = threading.Lock()
_provider_failures: dict[str, int] = {}


def batch_storage_root(artifact_root: Path, batch_id: str) -> Path:
    digest = hashlib.sha256(batch_id.encode("utf-8")).hexdigest()[:32]
    return (artifact_root.resolve() / "recognition_batches" / digest).resolve()


def _staged_image_path(artifact_root: Path, batch_id: str, relative_path: str) -> Path:
    root = batch_storage_root(artifact_root, batch_id)
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Invalid recognition batch image path")
    return path


def _stage_images(
    artifact_root: Path,
    batch_id: str,
    images: list[tuple[str, bytes]],
) -> list[dict[str, Any]]:
    root = batch_storage_root(artifact_root, batch_id)
    input_root = root / "inputs"
    input_root.mkdir(parents=True, exist_ok=False)
    records: list[dict[str, Any]] = []
    try:
        for index, (filename, image_bytes) in enumerate(images):
            relative_path = f"inputs/{index:04d}.upload"
            destination = _staged_image_path(artifact_root, batch_id, relative_path)
            handle = tempfile.NamedTemporaryFile(
                dir=input_root,
                prefix=f".{index:04d}-",
                suffix=".tmp",
                delete=False,
            )
            temporary = Path(handle.name)
            try:
                handle.write(image_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                handle.close()
            os.replace(temporary, destination)
            records.append(
                {
                    "index": index,
                    "filename": filename,
                    "input_relative_path": relative_path,
                    "input_sha256": hashlib.sha256(image_bytes).hexdigest(),
                }
            )
    except BaseException:
        if root.is_relative_to(artifact_root.resolve()) and root.is_dir():
            shutil.rmtree(root)
        raise
    return records


def _batch_item_id(batch_id: str, item_index: int) -> str:
    return str(uuid.uuid5(ITEM_NAMESPACE, f"{batch_id}:{item_index}"))


def _load_batch(database_path: str, batch_id: str) -> dict[str, Any] | None:
    with database_session(database_path) as connection:
        return batches.get_batch(connection, batch_id)


def _record_provider_result(batch_id: str, reason: str) -> bool:
    with _runtime_lock:
        if reason == REASON_VISION_UNAVAILABLE:
            count = _provider_failures.get(batch_id, 0) + 1
            _provider_failures[batch_id] = count
            return count >= PROVIDER_CIRCUIT_BREAK_LIMIT
        _provider_failures[batch_id] = 0
        return False


def _clear_runtime(batch_id: str) -> None:
    with _runtime_lock:
        _provider_failures.pop(batch_id, None)


def _finish_and_advance(
    *,
    database_path: str,
    batch_id: str,
    wall_ms: float,
    model_dir: Path,
) -> None:
    with database_session(database_path) as connection:
        snapshot, should_embed = batches.refresh_progress(
            connection,
            batch_id=batch_id,
            wall_ms=wall_ms,
        )
    if should_embed:
        _embedding_executor.submit(
            _embed_batch_items,
            database_path=database_path,
            batch_id=batch_id,
            model_dir=model_dir,
        )
    elif snapshot and snapshot["status"] in batches.TERMINAL_BATCH_STATUSES:
        _clear_runtime(batch_id)


def _process_image(
    *,
    batch_id: str,
    item_index: int,
    vision_client: VisionClientLike,
    database_path: str,
    artifact_root: Path,
    model_dir: Path,
) -> None:
    started = time.perf_counter()
    with database_session(database_path) as connection:
        item = batches.claim_item(connection, batch_id, item_index)
        batch = batches.get_batch(connection, batch_id)
    if item is None or batch is None:
        return

    result: dict[str, Any] = {
        "status": "failed",
        "reason": REASON_INTERNAL_ERROR,
        "wardrobe_item_id": "",
        "item_type": "",
        "subtype": "",
        "color": "",
        "name": "",
        "confidence": 0.0,
        "attributes": None,
    }
    try:
        image_path = _staged_image_path(
            artifact_root,
            batch_id,
            item["input_relative_path"],
        )
        image_bytes = image_path.read_bytes()
        raw = vision_client.analyze_image(image_bytes, build_analysis_prompt())
        attributes = parse_attributes(raw)
        item_type, subtype = map_ai_type_to_item_fields(attributes.type, attributes.subtype)
        result.update(
            {
                "item_type": item_type,
                "subtype": subtype,
                "color": attributes.primary_color,
                "name": attributes.description[:32] if attributes.description else "",
                "confidence": attributes.confidence,
                "attributes": attributes_to_dict(attributes),
            }
        )
        if not is_reliable_analysis(attributes):
            result["reason"] = REASON_LOW_CONFIDENCE
        else:
            created = create_photo_item(
                database_path=database_path,
                artifact_root=artifact_root,
                user_id=batch["user_id"],
                image_bytes=image_bytes,
                model_dir=model_dir,
                device="cuda",
                name=result["name"],
                item_type=item_type,
                subtype=subtype,
                color=result["color"],
                gender=batch["gender"],
                attributes=result["attributes"],
                item_id=_batch_item_id(batch_id, item_index),
                skip_embedding=True,
                skip_initialize=True,
            )
            result.update(
                {
                    "status": "succeeded",
                    "reason": "",
                    "wardrobe_item_id": created["item_id"],
                }
            )
    except VisionUnavailable:
        result["reason"] = REASON_VISION_UNAVAILABLE
    except VisionInvalidJson:
        result["reason"] = REASON_INVALID_JSON
    except OSError:
        result["reason"] = REASON_INVALID_IMAGE
    except (ValueError, RuntimeError):
        result["reason"] = REASON_CREATE_FAILED
    except Exception:  # noqa: BLE001 - every persisted item must reach a safe state
        logger.exception(
            "recognition_item_failed batch_id=%s item_index=%s",
            batch_id,
            item_index,
        )
        result["reason"] = REASON_INTERNAL_ERROR

    with database_session(database_path) as connection:
        batches.finish_item(
            connection,
            batch_id=batch_id,
            item_index=item_index,
            **result,
        )
        if _record_provider_result(batch_id, result["reason"]):
            batches.fail_pending_items(connection, batch_id, REASON_PROVIDER_UNAVAILABLE)
    _finish_and_advance(
        database_path=database_path,
        batch_id=batch_id,
        wall_ms=(time.perf_counter() - started) * 1000.0,
        model_dir=model_dir,
    )


def _embed_batch_items(
    *,
    database_path: str,
    batch_id: str,
    model_dir: Path,
) -> None:
    batch = _load_batch(database_path, batch_id)
    if batch is None:
        return
    item_ids = [result["item_id"] for result in batch["results"] if result["item_id"]]
    try:
        embedding = embed_personal_items(
            database_path=database_path,
            item_ids=item_ids,
            model_dir=model_dir,
            device="cuda",
        )
        embedding["retryable"] = False
    except Exception as error:  # noqa: BLE001 - wardrobe commits stay durable
        logger.exception(
            "recognition_batch_embedding_failed batch_id=%s item_count=%s error_type=%s",
            batch_id,
            len(item_ids),
            type(error).__name__,
        )
        embedding = {
            "status": "failed",
            "error_code": "PERSONAL_EMBEDDING_FAILED",
            "error": "衣物已保存，但向量生成失败，可稍后重试",
            "retryable": True,
            "wardrobe_commit_preserved": True,
            "requested_items": len(item_ids),
        }
    with database_session(database_path) as connection:
        batches.finish_embedding(connection, batch_id=batch_id, embedding=embedding)
    _clear_runtime(batch_id)


def _submit_pending(
    *,
    database_path: str,
    batch_id: str,
    artifact_root: Path,
    model_dir: Path,
    vision_client: VisionClientLike,
) -> int:
    batch = _load_batch(database_path, batch_id)
    if batch is None:
        return 0
    pending = [result for result in batch["results"] if result["status"] == "pending"]
    for result in pending:
        _recognition_executor.submit(
            _process_image,
            batch_id=batch_id,
            item_index=result["index"],
            vision_client=vision_client,
            database_path=database_path,
            artifact_root=artifact_root,
            model_dir=model_dir,
        )
    return len(pending)


def start_batch(
    *,
    user_id: str,
    images: list[tuple[str, bytes]],
    default_gender: str,
    vision_client: VisionClientLike,
    database_path: str,
    artifact_root: Path,
    model_dir: Path,
    auto_embed: bool = True,
) -> dict[str, Any]:
    initialize_database(database_path)
    batch_id = f"recognition:{uuid.uuid4()}"
    records = _stage_images(artifact_root, batch_id, images)
    try:
        with database_session(database_path) as connection:
            batches.create_batch(
                connection,
                batch_id=batch_id,
                user_id=user_id,
                gender=default_gender,
                auto_embed=auto_embed,
                items=records,
            )
            snapshot = batches.get_batch(connection, batch_id)
    except BaseException:
        root = batch_storage_root(artifact_root, batch_id)
        if root.is_relative_to(artifact_root.resolve()) and root.is_dir():
            shutil.rmtree(root)
        raise
    _submit_pending(
        database_path=database_path,
        batch_id=batch_id,
        artifact_root=artifact_root,
        model_dir=model_dir,
        vision_client=vision_client,
    )
    if snapshot is None:
        raise RuntimeError("Recognition batch was not persisted")
    return snapshot


def get_batch(database_path: str, batch_id: str) -> dict[str, Any] | None:
    return _load_batch(database_path, batch_id)


def list_batches(database_path: str, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    with database_session(database_path) as connection:
        return batches.list_batches(connection, user_id, limit)


def retry_batch_embedding(
    *,
    user_id: str,
    batch_id: str,
    database_path: str,
    model_dir: Path,
) -> dict[str, Any] | None:
    with database_session(database_path) as connection:
        batch = batches.get_batch(connection, batch_id)
        if batch is None or batch["user_id"] != user_id:
            return None
        if not batches.start_embedding_retry(connection, batch_id):
            return None
        snapshot = batches.get_batch(connection, batch_id)
    _embedding_executor.submit(
        _embed_batch_items,
        database_path=database_path,
        batch_id=batch_id,
        model_dir=model_dir,
    )
    return snapshot


def retry_batch(
    *,
    user_id: str,
    batch_id: str,
    vision_client: VisionClientLike,
    database_path: str,
    artifact_root: Path,
    model_dir: Path,
) -> dict[str, Any] | None:
    with database_session(database_path) as connection:
        batch = batches.get_batch(connection, batch_id)
        if batch is None or batch["user_id"] != user_id:
            return None
        retryable = any(
            result["status"] in {"failed", "pending"} for result in batch["results"]
        ) and batch["status"] in {"partial_failed", "interrupted"}
        if not retryable:
            return None
        batches.reset_failed_items(connection, batch_id)
        batches.mark_batch_recovering(connection, batch_id)
        snapshot = batches.get_batch(connection, batch_id)
    _clear_runtime(batch_id)
    _submit_pending(
        database_path=database_path,
        batch_id=batch_id,
        artifact_root=artifact_root,
        model_dir=model_dir,
        vision_client=vision_client,
    )
    return snapshot


def resume_incomplete_batches(
    *,
    vision_client: VisionClientLike | None,
    database_path: str,
    artifact_root: Path,
    model_dir: Path,
) -> dict[str, int]:
    with database_session(database_path) as connection:
        batch_ids = batches.list_recoverable_batch_ids(connection)
    resumed = 0
    interrupted = 0
    for batch_id in batch_ids:
        batch = _load_batch(database_path, batch_id)
        if batch is None:
            continue
        if batch["status"] == "embedding":
            _embedding_executor.submit(
                _embed_batch_items,
                database_path=database_path,
                batch_id=batch_id,
                model_dir=model_dir,
            )
            resumed += 1
            continue
        if vision_client is None:
            with database_session(database_path) as connection:
                batches.mark_interrupted(connection, batch_id, "VISION_UNAVAILABLE")
            interrupted += 1
            continue
        with database_session(database_path) as connection:
            batches.mark_batch_recovering(connection, batch_id)
        resumed += int(
            _submit_pending(
                database_path=database_path,
                batch_id=batch_id,
                artifact_root=artifact_root,
                model_dir=model_dir,
                vision_client=vision_client,
            )
            > 0
        )
    return {"recoverable": len(batch_ids), "resumed": resumed, "interrupted": interrupted}


def delete_batch(
    *,
    user_id: str,
    batch_id: str,
    database_path: str,
    artifact_root: Path,
) -> bool:
    with database_session(database_path) as connection:
        deleted = batches.delete_batch(connection, user_id, batch_id)
    if not deleted:
        return False
    root = batch_storage_root(artifact_root, batch_id)
    if root.is_relative_to(artifact_root.resolve()) and root.is_dir():
        shutil.rmtree(root)
    _clear_runtime(batch_id)
    return True


def batch_input_path(
    *,
    user_id: str,
    batch_id: str,
    item_index: int,
    database_path: str,
    artifact_root: Path,
) -> Path | None:
    batch = _load_batch(database_path, batch_id)
    if batch is None or batch["user_id"] != user_id:
        return None
    item = next((item for item in batch["results"] if item["index"] == item_index), None)
    if item is None:
        return None
    path = _staged_image_path(artifact_root, batch_id, item["input_relative_path"])
    return path if path.is_file() else None
