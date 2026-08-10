"""In-process background batches for multi-modal wardrobe photo recognition.

The batch endpoints submit each image to a fixed-size thread pool
(``CONCURRENCY`` = 3) so several uploads are recognized in parallel instead of
serializing one request at a time. Recognized-and-reliable photos are written
straight into the user's wardrobe; failures are recorded per-image with a
``reason`` the front end renders as a "manual add" affordance.

Batches live only in this process: a backend restart drops in-flight tasks.
This is an accepted trade-off for a local-first tool (see README).
"""

from __future__ import annotations

import copy
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

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
DEFAULT_PER_IMAGE_MS = 8000.0
MAX_BATCHES_KEPT = 100
PROVIDER_CIRCUIT_BREAK_LIMIT = 2

# Per-image failure reasons the front end maps to Chinese copy and buttons.
REASON_LOW_CONFIDENCE = "low_confidence"
REASON_VISION_UNAVAILABLE = "vision_unavailable"
REASON_INVALID_JSON = "invalid_json"
REASON_INVALID_IMAGE = "invalid_image"
REASON_CREATE_FAILED = "create_failed"
REASON_PROVIDER_UNAVAILABLE = "provider_unavailable"
REASON_INTERNAL_ERROR = "internal_error"


class VisionClientLike(Protocol):
    """The slice of the vision client the batch worker uses."""

    def analyze_image(self, image_bytes: bytes, prompt: str) -> dict[str, Any]: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class BatchImageResult:
    """Outcome of recognizing one uploaded photo."""

    index: int
    filename: str
    status: str = "pending"  # pending | succeeded | failed
    reason: str = ""
    item_id: str = ""
    item_type: str = ""
    subtype: str = ""
    color: str = ""
    name: str = ""
    confidence: float = 0.0
    attributes: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "filename": self.filename,
            "status": self.status,
            "reason": self.reason,
            "item_id": self.item_id,
            "item_type": self.item_type,
            "subtype": self.subtype,
            "color": self.color,
            "name": self.name,
            "confidence": self.confidence,
            "attributes": self.attributes,
        }


@dataclass
class RecognitionBatch:
    """Snapshot of one batch recognition task (guarded by ``_store_lock``)."""

    batch_id: str
    user_id: str
    total: int
    started_at: str
    done: int = 0
    succeeded: int = 0
    failed: int = 0
    status: str = "running"  # running | completed
    finished_at: str | None = None
    eta_seconds: int = 0
    ema_ms: float = DEFAULT_PER_IMAGE_MS
    consecutive_provider_failures: int = 0
    results: list[BatchImageResult] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        """Serialize a point-in-time view for the poll endpoint."""
        self.eta_seconds = max(
            0,
            round(self.ema_ms * (self.total - self.done) / CONCURRENCY / 1000),
        )
        percent = round(self.done * 100 / self.total) if self.total else 100
        return {
            "batch_id": self.batch_id,
            "user_id": self.user_id,
            "total": self.total,
            "done": self.done,
            "percent": percent,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "status": self.status,
            "eta_seconds": self.eta_seconds,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "results": [result.to_dict() for result in self.results],
        }


_executor = ThreadPoolExecutor(max_workers=CONCURRENCY, thread_name_prefix="vision-batch")
_store: dict[str, RecognitionBatch] = {}
_store_lock = threading.Lock()


def _record_result(
    batch: RecognitionBatch,
    index: int,
    result: BatchImageResult,
    wall_ms: float,
) -> None:
    """Update batch counters, EMA timing, and circuit-break state under lock."""
    with _store_lock:
        # Once the batch is completed (e.g. by the circuit breaker), a worker
        # finishing late must not recount its failure or overwrite the
        # breaker-marked result.
        if batch.status != "running":
            return
        if result.status == "succeeded":
            batch.succeeded += 1
            batch.consecutive_provider_failures = 0
        else:
            batch.failed += 1
            if result.reason == REASON_VISION_UNAVAILABLE:
                batch.consecutive_provider_failures += 1
            else:
                batch.consecutive_provider_failures = 0
        batch.results[index] = result
        batch.done += 1
        if batch.done == 1 or batch.ema_ms == DEFAULT_PER_IMAGE_MS:
            batch.ema_ms = wall_ms
        else:
            batch.ema_ms = batch.ema_ms * 0.7 + wall_ms * 0.3

        if batch.done >= batch.total:
            batch.status = "completed"
            batch.finished_at = _now()
            return
        if batch.consecutive_provider_failures >= PROVIDER_CIRCUIT_BREAK_LIMIT:
            # Stop wasting time when the provider is down: mark everything the
            # workers have not touched yet as provider_unavailable.
            for pending in batch.results:
                if pending.status == "pending":
                    pending.status = "failed"
                    pending.reason = REASON_PROVIDER_UNAVAILABLE
                    batch.failed += 1
                    batch.done += 1
            batch.status = "completed"
            batch.finished_at = _now()


def _process_image(
    *,
    batch: RecognitionBatch,
    index: int,
    filename: str,
    image_bytes: bytes,
    default_gender: str,
    vision_client: VisionClientLike,
    database_path: Path,
    artifact_root: Path,
    model_dir: Path,
) -> None:
    """Recognize one photo and auto-add it to the wardrobe when reliable."""
    started = time.perf_counter()
    result = BatchImageResult(index=index, filename=filename)
    try:
        raw = vision_client.analyze_image(image_bytes, build_analysis_prompt())
        attributes = parse_attributes(raw)
        item_type, subtype = map_ai_type_to_item_fields(
            attributes.type, attributes.subtype
        )
        result.confidence = attributes.confidence
        result.item_type = item_type
        result.subtype = subtype
        result.color = attributes.primary_color
        result.name = attributes.description[:32] if attributes.description else ""
        result.attributes = attributes_to_dict(attributes)
        if not is_reliable_analysis(attributes):
            result.status = "failed"
            result.reason = REASON_LOW_CONFIDENCE
            return
        created = create_photo_item(
            database_path=database_path,
            artifact_root=artifact_root,
            user_id=batch.user_id,
            image_bytes=image_bytes,
            model_dir=model_dir,
            device="cuda",
            name=result.name,
            item_type=item_type,
            subtype=subtype,
            color=result.color,
            gender=default_gender,
            attributes=result.attributes,
            skip_embedding=True,
            skip_initialize=True,
        )
        result.status = "succeeded"
        result.item_id = created["item_id"]
    except VisionUnavailable:
        result.status = "failed"
        result.reason = REASON_VISION_UNAVAILABLE
    except VisionInvalidJson:
        result.status = "failed"
        result.reason = REASON_INVALID_JSON
    except OSError:
        result.status = "failed"
        result.reason = REASON_INVALID_IMAGE
    except (ValueError, RuntimeError):
        result.status = "failed"
        result.reason = REASON_CREATE_FAILED
    except Exception:  # noqa: BLE001 - the batch must always reach completed
        result.status = "failed"
        result.reason = REASON_INTERNAL_ERROR
    finally:
        _record_result(batch, index, result, (time.perf_counter() - started) * 1000.0)


def start_batch(
    *,
    user_id: str,
    images: list[tuple[str, bytes]],
    default_gender: str,
    vision_client: VisionClientLike,
    database_path: Path,
    artifact_root: Path,
    model_dir: Path,
) -> RecognitionBatch:
    """Register a batch and kick off recognition for every image in the pool."""
    batch = RecognitionBatch(
        batch_id=f"recognition:{uuid.uuid4()}",
        user_id=user_id,
        total=len(images),
        started_at=_now(),
        results=[
            BatchImageResult(index=index, filename=filename)
            for index, (filename, _) in enumerate(images)
        ],
    )
    with _store_lock:
        _store[batch.batch_id] = batch
        if len(_store) > MAX_BATCHES_KEPT:
            completed_ids = [
                batch_id
                for batch_id, item in _store.items()
                if item.status == "completed"
            ]
            for batch_id in completed_ids[: len(_store) - MAX_BATCHES_KEPT]:
                del _store[batch_id]

    for index, (filename, image_bytes) in enumerate(images):
        _executor.submit(
            _process_image,
            batch=batch,
            index=index,
            filename=filename,
            image_bytes=image_bytes,
            default_gender=default_gender,
            vision_client=vision_client,
            database_path=database_path,
            artifact_root=artifact_root,
            model_dir=model_dir,
        )
    return batch


def get_batch(batch_id: str) -> RecognitionBatch | None:
    """Return a deep copy of a batch so readers never race the workers."""
    with _store_lock:
        batch = _store.get(batch_id)
        return copy.deepcopy(batch) if batch is not None else None


def list_batches(user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Summaries of a user's batches, newest first (deep copies, no race)."""
    with _store_lock:
        owned = [
            copy.deepcopy(batch)
            for batch in _store.values()
            if batch.user_id == user_id
        ]
    owned.sort(key=lambda batch: batch.started_at, reverse=True)
    return [batch.snapshot() for batch in owned[:limit]]
