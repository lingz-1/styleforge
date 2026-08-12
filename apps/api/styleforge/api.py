"""FastAPI application for wardrobe management and multi-agent recommendations."""

from __future__ import annotations

import base64
import binascii
import json
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from styleforge.core.config import Settings
from styleforge.core.taxonomy import build_taxonomy
from styleforge.llm.client import LlmInvalidJson, LlmSchemaViolation, LlmUnavailable
from styleforge.llm.extension_prompts import EXTENSION_PROMPT_VERSION
from styleforge.integrations.embeddings.text_embedder import TextEmbedder
from styleforge.integrations.vectorstores.chroma_store import ChromaStore
from styleforge.models.task import TaskExecutionInput
from styleforge.orchestration.graph import MultiTaskGraph
from styleforge.orchestration.task_router import TaskType
from styleforge.repositories.database import database_session, initialize_database
from styleforge.tools.weather.schemas import (
    DeviceLocationContext,
    WeatherFacts,
    WeatherToolInput,
)
from styleforge.repositories.dataset_source_repository import (
    get_source_image_root,
    list_dataset_sources,
)
from styleforge.repositories.wardrobe_import_repository import (
    commit_import_rows,
    create_import_preview,
    get_import_batch,
    list_import_rows,
    personal_image_root,
)
from styleforge.repositories.user_preferences_repository import (
    get_evaluation_weights,
    save_evaluation_weights,
)
from styleforge.repositories.chat_repository import (
    append_message,
    create_chat_session,
    delete_chat_session,
    get_chat_session,
    list_chat_sessions,
    list_messages,
    rename_chat_session,
)
from styleforge.repositories.interaction_event_repository import record_event
from styleforge.repositories.preference_model_repository import (
    get_preference,
    get_preference_by_key,
    list_preferences,
    soft_forget,
    upsert_preference,
)
from styleforge.services.memory_aggregator import apply_evidence
from styleforge.services.memory_consolidation import consolidate_session
from styleforge.services.memory_evidence import behavior_evidence_from_event
from styleforge.repositories.task_run_repository import get_task_run
from styleforge.core.redis import delete_session_outfit_cache, redis_client_from_settings
from styleforge.services.chat_service import (
    assistant_summary,
    cache_session_outfit,
    get_session_outfit_context,
    outfit_context_from_payload,
    trim_message_payload,
)
from styleforge.services.order_import import parse_order_workbook
from styleforge.services.personal_embeddings import embed_personal_items
from styleforge.services.recognition_batch import (
    delete_batch,
    get_batch,
    list_batches,
    start_batch,
)
from styleforge.services.personal_images import bind_personal_image
from styleforge.services.wardrobe_item_service import (
    create_photo_item,
    update_personal_item,
)
from styleforge.vision.clothing_analysis import (
    attributes_to_dict,
    build_analysis_prompt,
    is_reliable_analysis,
    map_ai_type_to_item_fields,
    parse_attributes,
)
from styleforge.vision.vision_client import (
    VisionInvalidJson,
    VisionUnavailable,
    vision_client_from_settings,
)
from styleforge.workflow.graph import StyleForgeWorkflow
from styleforge.workflow.task_workflow import MultiTaskWorkflow


class RecommendationRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    request: str = Field(min_length=1, max_length=2000)
    max_results: int = Field(default=3, ge=1, le=10)
    location_context: DeviceLocationContext | None = None


class TaskRoutingRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    request: str = Field(min_length=1, max_length=2000)
    current_outfit_id: str = Field(default="", max_length=128)
    has_candidate_item: bool = False
    task_type: TaskType | None = None


class WardrobeItemRequest(BaseModel):
    item_id: str = Field(min_length=1, max_length=128)


class OrderWorkbookUpload(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=30_000_000)
    default_audience: str = Field(default="", max_length=16)


class WardrobeImportSelection(BaseModel):
    row_id: str = Field(min_length=1, max_length=128)
    item_type: str | None = Field(default=None, max_length=64)
    subtype: str | None = Field(default=None, max_length=64)
    color: str | None = Field(default=None, max_length=64)
    size: str | None = Field(default=None, max_length=64)
    audience: str | None = Field(default=None, max_length=16)


class WardrobeImportCommitRequest(BaseModel):
    selections: list[WardrobeImportSelection] = Field(min_length=1, max_length=2000)
    auto_embed: bool = True


class PersonalImageUpload(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=30_000_000)


class EvaluationWeightsRequest(BaseModel):
    weights: dict[str, float]


class PhotoItemRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=30_000_000)
    item_type: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=128)
    subtype: str = Field(default="", max_length=64)
    color: str = Field(default="", max_length=64)
    gender: str = Field(default="women", max_length=16)
    size: str = Field(default="", max_length=16)
    attributes: dict | None = None


class BatchRecognitionRequest(BaseModel):
    images: list[PersonalImageUpload] = Field(min_length=1, max_length=30)
    default_gender: str = Field(default="women", max_length=16)


class UpdateItemRequest(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    item_type: str | None = Field(default=None, max_length=64)
    subtype: str | None = Field(default=None, max_length=64)
    color: str | None = Field(default=None, max_length=64)
    gender: str | None = Field(default=None, max_length=16)
    size: str | None = Field(default=None, max_length=16)
    attributes: dict | None = None


class ChatSessionCreate(BaseModel):
    title: str = Field(default="", max_length=128)


class ChatSessionRename(BaseModel):
    title: str = Field(min_length=1, max_length=128)


class MemoryCreate(BaseModel):
    """An explicit preference statement: a dimensioned claim the user confirms.

    Persisted as strong explicit evidence (source=explicit_statement) and
    aggregated into the preference model, which promotes it to long-term.
    """

    dimension: str = Field(default="shopping", max_length=32)
    attribute: str = Field(min_length=1, max_length=32)
    value: str = Field(min_length=1, max_length=64)
    polarity: str = Field(default="positive", pattern="^(positive|negative)$")
    strength: float = Field(default=0.9, ge=0.0, le=1.0)


class MemoryUpdate(BaseModel):
    """Direct edit of a preference row (the model is the current hypothesis)."""

    dimension: str | None = Field(default=None, min_length=1, max_length=32)
    attribute: str | None = Field(default=None, min_length=1, max_length=32)
    value: str | None = Field(default=None, min_length=1, max_length=64)
    polarity: str | None = Field(default=None, pattern="^(positive|negative)$")
    lifecycle: str | None = Field(
        default=None,
        pattern="^(short_term|long_term_candidate|long_term)$",
    )


class BehaviorEventCreate(BaseModel):
    """A raw user behavior event reported by the front end / instrumentation."""

    event_type: str = Field(min_length=1, max_length=40)
    item_id: str = Field(default="", max_length=128)
    context: dict[str, Any] = Field(default_factory=dict)
    features: dict[str, Any] = Field(default_factory=dict)


settings = Settings.from_env()
API_STARTED_AT = datetime.now(timezone.utc).isoformat()
initialize_database(settings.database_dsn)
# Optional Redis session-outfit cache (disabled/unreachable -> None, degraded).
_redis_client = redis_client_from_settings(settings)
app = FastAPI(
    title="StyleForge API",
    version="0.3.0",
    description="Local-first multi-agent personal wardrobe styling API.",
)


@lru_cache(maxsize=1)
def get_workflow() -> StyleForgeWorkflow:
    return StyleForgeWorkflow(
        database_path=settings.database_dsn,
        embedding_dir=settings.embedding_dir,
        model_dir=settings.artifact_root / "models",
        device="cuda",
    )


@lru_cache(maxsize=1)
def get_task_graph() -> MultiTaskGraph:
    return MultiTaskGraph()


_knowledge_chroma: dict[str, Any] = {}


def _ensure_knowledge_chroma() -> tuple[Any, Any] | None:
    """Lazily build the Chroma store + text embedder; ``None`` on any failure.

    Mirrors ``_ensure_vision``: a missing/empty index or a model-load failure
    degrades knowledge retrieval to keyword matching rather than failing the
    request. Text embedding prefers CUDA and falls back to CPU.
    """
    if _knowledge_chroma.get("attempted"):
        return _knowledge_chroma.get("value")
    _knowledge_chroma["attempted"] = True
    try:
        store = ChromaStore(settings.chroma_dir)
        try:
            embedder = TextEmbedder(
                settings.artifact_root / "models", device="cuda", precision="float16"
            )
        except BaseException:
            embedder = TextEmbedder(
                settings.artifact_root / "models", device="cpu", precision="float32"
            )
        value: tuple[Any, Any] | None = (store, embedder)
    except BaseException:
        value = None
    _knowledge_chroma["value"] = value
    return value


@lru_cache(maxsize=1)
def get_multi_task_workflow() -> MultiTaskWorkflow:
    workflow = get_workflow()
    chroma = _ensure_knowledge_chroma()
    return MultiTaskWorkflow(
        database_path=settings.database_dsn,
        knowledge_root=settings.knowledge_root,
        llm_client=workflow.llm_client,
        recommendation_runner=lambda **kwargs: workflow.recommend_payload(**kwargs),
        chroma_store=chroma[0] if chroma else None,
        text_embedder=chroma[1] if chroma else None,
    )


def _catalog_row_to_dict(row) -> dict[str, Any]:
    return {
        "item_id": row["item_id"],
        "source": row["source"],
        "gender": row["gender"],
        "item_type": row["item_type"],
        "main_category": row["main_category"],
        "name": row["name"],
        "color": row["color"],
        "description": row["description"],
        "relative_image_path": row["relative_image_path"],
        "image_status": row["image_status"],
        "embedding_status": row["embedding_status"],
        "attributes": json.loads(row["attributes_json"] or "{}"),
        "image_url": f"/items/{row['item_id']}/image",
    }


def _decode_base64(content: str, *, maximum_bytes: int) -> bytes:
    try:
        payload = base64.b64decode(content, validate=True)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(status_code=422, detail="Invalid base64 payload") from error
    if len(payload) > maximum_bytes:
        raise HTTPException(status_code=413, detail="Uploaded file is too large")
    return payload


@app.get("/health")
def health() -> dict[str, Any]:
    embedding_manifest = settings.embedding_dir / "manifest.json"
    index_manifest = settings.index_dir / "manifest.json"
    with database_session(settings.database_dsn) as connection:
        catalog_count = connection.execute("SELECT COUNT(*) FROM catalog_items").fetchone()[0]
        ready_count = connection.execute(
            "SELECT COUNT(*) FROM catalog_items WHERE embedding_status = 'ready'"
        ).fetchone()[0]
        personal_embedding_count = connection.execute(
            "SELECT COUNT(*) FROM personal_item_embeddings"
        ).fetchone()[0]
        source_counts = {
            row["source"]: row["count"]
            for row in connection.execute(
                "SELECT source, COUNT(*) AS count FROM catalog_items GROUP BY source"
            )
        }
        image_sources = {
            row["source"]: {
                "image_root": row["image_root"],
                "available": Path(row["image_root"]).is_dir(),
            }
            for row in list_dataset_sources(connection)
        }
        if settings.image_root is not None and "polyvore" not in image_sources:
            image_sources["polyvore"] = {
                "image_root": str(settings.image_root),
                "available": settings.image_root.is_dir(),
                "configuration": "environment_fallback",
            }
    return {
        "status": "ok",
        "api_started_at": API_STARTED_AT,
        "extension_prompt_version": EXTENSION_PROMPT_VERSION,
        "weather": {
            "enabled": settings.weather_enabled,
            "provider": settings.weather_provider,
            "default_location_configured": bool(
                settings.weather_default_location
            ),
            "location_max_age_seconds": settings.location_max_age_seconds,
            "location_max_accuracy_m": settings.location_max_accuracy_m,
            "reverse_geocode_configured": bool(
                settings.reverse_geocode_endpoint
            ),
        },
        "database": str(settings.database_dsn),
        "catalog_items": catalog_count,
        "embedding_ready_items": ready_count,
        "personal_embedding_items": personal_embedding_count,
        "catalog_items_by_source": source_counts,
        "image_sources": image_sources,
        "embedding_manifest_available": embedding_manifest.is_file(),
        "index_manifest_available": index_manifest.is_file(),
        "image_root_configured": bool(image_sources),
    }


@app.get("/weather/now")
def weather_now(
    location: str | None = Query(default=None, max_length=160),
    latitude: float | None = Query(default=None, ge=-90, le=90),
    longitude: float | None = Query(default=None, ge=-180, le=180),
) -> dict[str, Any]:
    """Today's weather for the device location, a named location, or the default city.

    The home card fetches this on load: device coordinates (when the user has
    granted geolocation) take priority, then an explicit city name, then the
    configured default city. An honest ``unavailable`` payload is returned
    instead of a 500 when nothing is resolvable.
    """
    has_location = bool((location or "").strip())
    has_lat = latitude is not None
    has_lon = longitude is not None
    if has_location and (has_lat or has_lon):
        raise HTTPException(
            status_code=422, detail="location 与坐标互斥，只能提供其中一种"
        )
    if has_lat != has_lon:
        raise HTTPException(
            status_code=422, detail="latitude 和 longitude 必须同时提供"
        )

    today_iso = date.today().isoformat()
    tool = get_workflow().weather_tool
    if tool is None:
        return WeatherFacts.unavailable(
            requested_location="",
            requested_date=today_iso,
            error_code="weather_disabled",
            error_message="天气功能未启用",
        ).model_dump(mode="json")

    if has_location:
        tool_input = WeatherToolInput(location=location.strip(), date=today_iso)
    elif has_lat:
        tool_input = WeatherToolInput(
            latitude=latitude,
            longitude=longitude,
            date=today_iso,
        )
    elif settings.weather_default_location:
        tool_input = WeatherToolInput(
            location=settings.weather_default_location,
            date=today_iso,
        )
    else:
        return WeatherFacts.unavailable(
            requested_location="",
            requested_date=today_iso,
            error_code="location_required",
            error_message="未提供地点，也未配置默认城市（STYLEFORGE_DEFAULT_LOCATION）",
        ).model_dump(mode="json")

    facts = tool(tool_input)
    return facts.model_dump(mode="json")


@app.get("/preferences/{user_id}/evaluation")
def get_user_evaluation_weights(user_id: str) -> dict[str, Any]:
    with database_session(settings.database_dsn) as connection:
        weights = get_evaluation_weights(connection, user_id)
    return {"user_id": user_id, "weights": weights}


@app.put("/preferences/{user_id}/evaluation")
def put_user_evaluation_weights(
    user_id: str,
    request: EvaluationWeightsRequest,
) -> dict[str, Any]:
    with database_session(settings.database_dsn) as connection:
        normalized = save_evaluation_weights(connection, user_id, request.weights)
    # Weak explicit signal: which dimensions the user emphasizes.
    _try_behavior_event(
        user_id=user_id,
        event_type="explicit_preference",
        features={
            "dimension": "shopping",
            "attribute": "evaluation_weight",
            "value": ",".join(sorted(request.weights)),
            "polarity": "positive",
            "strength": 0.3,
        },
    )
    return {"user_id": user_id, "weights": normalized}


def _fold_behavior_event(
    connection,
    user_id: str,
    event_type: str,
    *,
    item_id: str = "",
    context: dict[str, Any] | None = None,
    features: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record one raw behavior event, then fold its evidence into the model."""
    event = record_event(
        connection,
        user_id,
        event_type,
        item_id=item_id,
        context=context,
        features=features,
    )
    evidence = behavior_evidence_from_event(connection, event)
    apply_evidence(connection, user_id, evidence)
    return event


def _try_behavior_event(
    *,
    user_id: str,
    event_type: str,
    item_id: str = "",
    context: dict[str, Any] | None = None,
    features: dict[str, Any] | None = None,
) -> None:
    """Best-effort instrumentation: memory recording never breaks the request."""
    try:
        with database_session(settings.database_dsn) as connection:
            _fold_behavior_event(
                connection,
                user_id,
                event_type,
                item_id=item_id,
                context=context,
                features=features,
            )
    except BaseException:
        pass


@app.get("/preferences/{user_id}/memories")
def list_user_memories(
    user_id: str,
    lifecycle: str | None = Query(default=None, max_length=32),
) -> dict[str, Any]:
    with database_session(settings.database_dsn) as connection:
        preferences = list_preferences(connection, user_id, lifecycle=lifecycle)
    return {"user_id": user_id, "count": len(preferences), "memories": preferences}


@app.post("/preferences/{user_id}/memories", status_code=201)
def create_user_memory(user_id: str, request: MemoryCreate) -> dict[str, Any]:
    """Create an explicit preference: strong evidence promoted to long-term."""
    try:
        with database_session(settings.database_dsn) as connection:
            event = _fold_behavior_event(
                connection,
                user_id,
                "explicit_preference",
                features={
                    "dimension": request.dimension,
                    "attribute": request.attribute,
                    "value": request.value,
                    "polarity": request.polarity,
                    "strength": request.strength,
                },
            )
            preference = get_preference_by_key(
                connection,
                user_id,
                request.dimension.lower(),
                request.attribute.lower(),
                request.value.lower(),
            )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"event_id": event["event_id"], "preference": preference}


@app.patch("/preferences/{user_id}/memories/{preference_id}")
def update_user_memory(
    user_id: str,
    preference_id: int,
    request: MemoryUpdate,
) -> dict[str, Any]:
    """Directly edit one preference row (the model is the current hypothesis)."""
    try:
        with database_session(settings.database_dsn) as connection:
            preference = get_preference(connection, user_id, preference_id)
            if preference is None:
                raise HTTPException(status_code=404, detail="Memory not found")
            dimension = (request.dimension or preference["dimension"]).lower()
            attribute = (request.attribute or preference["attribute"]).lower()
            value = (request.value or preference["value"]).lower()
            updated = upsert_preference(
                connection,
                user_id,
                dimension=dimension,
                attribute=attribute,
                value=value,
                polarity=request.polarity or preference["polarity"],
                lifecycle=request.lifecycle or preference["lifecycle"],
                scope=preference.get("scope"),
                confidence=preference.get("confidence", 0),
                support_score=preference.get("support_score", 0),
                contradiction_score=preference.get("contradiction_score", 0),
                support_count=preference.get("support_count", 0),
                contradiction_count=preference.get("contradiction_count", 0),
                source_summary=preference.get("source_summary"),
                decay_policy=preference.get("decay_policy", "normal"),
                last_observed_at=preference.get("last_observed_at"),
                expires_at=preference.get("expires_at", ""),
            )
            if (dimension, attribute, value) != (
                preference["dimension"],
                preference["attribute"],
                preference["value"],
            ):
                soft_forget(connection, user_id, preference_id)
    except HTTPException:
        raise
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return updated


@app.delete("/preferences/{user_id}/memories/{preference_id}")
def forget_user_memory(user_id: str, preference_id: int) -> dict[str, Any]:
    with database_session(settings.database_dsn) as connection:
        if not soft_forget(connection, user_id, preference_id):
            raise HTTPException(status_code=404, detail="Memory not found")
    return {"preference_id": preference_id, "forgotten": True}


@app.post("/users/{user_id}/events", status_code=201)
def record_user_event(user_id: str, request: BehaviorEventCreate) -> dict[str, Any]:
    """Record one raw behavior event and fold its deterministic evidence in.

    Front-end interaction buttons (adopt / replace / reject / feedback) and the
    existing endpoints' instrumentation both go through this path.
    """
    try:
        with database_session(settings.database_dsn) as connection:
            return _fold_behavior_event(
                connection,
                user_id,
                request.event_type,
                item_id=request.item_id,
                context=request.context,
                features=request.features,
            )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/users/{user_id}/chat-sessions", status_code=201)
def create_user_chat_session(
    user_id: str,
    request: ChatSessionCreate,
) -> dict[str, Any]:
    with database_session(settings.database_dsn) as connection:
        title = request.title.strip()
        if not title:
            session_count = connection.execute(
                "SELECT COUNT(*) FROM chat_sessions WHERE user_id = %s",
                (user_id,),
            ).fetchone()[0]
            title = f"会话 {session_count + 1}"
        session = create_chat_session(connection, user_id=user_id, title=title)
    return session


@app.get("/users/{user_id}/chat-sessions")
def list_user_chat_sessions(user_id: str) -> dict[str, Any]:
    with database_session(settings.database_dsn) as connection:
        sessions = list_chat_sessions(connection, user_id)
    return {"user_id": user_id, "count": len(sessions), "sessions": sessions}


@app.get("/chat-sessions/{session_id}")
def get_user_chat_session(
    session_id: str,
    user_id: str = Query(min_length=1, max_length=128),
) -> dict[str, Any]:
    with database_session(settings.database_dsn) as connection:
        session = get_chat_session(connection, user_id, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
        messages = list_messages(connection, session_id)
    return {**session, "messages": messages}


@app.patch("/chat-sessions/{session_id}")
def rename_user_chat_session(
    session_id: str,
    request: ChatSessionRename,
    user_id: str = Query(min_length=1, max_length=128),
) -> dict[str, Any]:
    with database_session(settings.database_dsn) as connection:
        session = rename_chat_session(connection, user_id, session_id, request.title)
    if session is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return session


@app.delete("/chat-sessions/{session_id}")
def delete_user_chat_session(
    session_id: str,
    user_id: str = Query(min_length=1, max_length=128),
) -> dict[str, Any]:
    with database_session(settings.database_dsn) as connection:
        if not delete_chat_session(connection, user_id, session_id):
            raise HTTPException(status_code=404, detail="Chat session not found")
    # Session ended: fold the accumulated evidence into a consistent model.
    try:
        with database_session(settings.database_dsn) as connection:
            consolidate_session(connection, user_id, session_id)
    except BaseException:
        pass
    delete_session_outfit_cache(_redis_client, session_id)
    return {"session_id": session_id, "deleted": True}


@app.post("/recommendations")
def recommend(request: RecommendationRequest) -> dict[str, Any]:
    try:
        payload = get_workflow().recommend_payload(
            user_id=request.user_id,
            request=request.request,
            max_results=request.max_results,
            location_context=(
                request.location_context.model_dump(exclude_none=True)
                if request.location_context is not None
                else None
            ),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except BaseException as error:
        raise HTTPException(
            status_code=500,
            detail=f"Recommendation workflow failed: {type(error).__name__}: {error}",
        ) from error
    payload["image_endpoint_template"] = "/items/{item_id}/image"
    return payload


@app.post("/tasks/route")
def route_task(request: TaskRoutingRequest) -> dict[str, Any]:
    """Classify one request without executing its v3.3 task subgraph."""
    try:
        return get_task_graph().route(
            user_id=request.user_id,
            request=request.request,
            current_outfit_id=request.current_outfit_id,
            has_candidate_item=request.has_candidate_item,
            requested_task_type=request.task_type,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _resolve_session_turn(request: TaskExecutionInput) -> dict[str, Any] | None:
    """Verify session ownership and persist the user message up front.

    Returns the session's current outfit context so a follow-up can reuse the
    produced outfit; a missing session is a 404 before any task work starts.
    """
    if not request.session_id:
        return None
    with database_session(settings.database_dsn) as connection:
        session = get_chat_session(connection, request.user_id, request.session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
        context = get_session_outfit_context(
            connection,
            request.user_id,
            request.session_id,
            redis=_redis_client,
            redis_ttl=settings.redis_ttl,
        )
        append_message(
            connection,
            session_id=request.session_id,
            user_id=request.user_id,
            role="user",
            content=request.request,
        )
    return context


def _append_chat_success(
    request: TaskExecutionInput,
    payload: dict[str, Any],
) -> str:
    """Persist the assistant turn with a trimmed, re-renderable result snapshot."""
    if not request.session_id:
        return ""
    with database_session(settings.database_dsn) as connection:
        outfit_context = outfit_context_from_payload(payload)
        message_id = append_message(
            connection,
            session_id=request.session_id,
            user_id=request.user_id,
            role="assistant",
            content=assistant_summary(payload),
            task_type=str(payload.get("task_type", "")),
            run_id=str(payload.get("run_id", "")),
            result_json={
                **trim_message_payload(payload),
                "outfit_context": outfit_context,
            },
        )
    # Warm the Redis session cache so the next follow-up skips the DB scan.
    cache_session_outfit(_redis_client, request.session_id, outfit_context, settings.redis_ttl)
    return message_id


def _append_chat_failure(request: TaskExecutionInput, detail: str) -> None:
    """Record the failure as an assistant message so the chain stays complete."""
    if not request.session_id:
        return
    with database_session(settings.database_dsn) as connection:
        append_message(
            connection,
            session_id=request.session_id,
            user_id=request.user_id,
            role="assistant",
            content=detail,
            result_json={
                "request": request.request,
                "task_type": (
                    request.requested_task_type.value
                    if request.requested_task_type is not None
                    else ""
                ),
                "status": "failed",
                "error": detail,
            },
        )


_TASK_TYPE_EVENTS = {
    "style_advice": "style_requested",
    "item_advice": "style_requested",
    "wardrobe_compatibility": "compatibility_checked",
    # OUTFIT_MODIFY is recorded inside task_workflow where the replaced
    # item ids are known (item_replaced).
}


def _record_task_event(request: TaskExecutionInput, payload: dict[str, Any]) -> None:
    """Record a raw event for the task type just executed (best-effort)."""
    task_type = str(payload.get("task_type") or "")
    event_type = _TASK_TYPE_EVENTS.get(task_type.lower())
    if not event_type:
        return
    _try_behavior_event(
        user_id=request.user_id,
        event_type=event_type,
        context={
            "request": request.request,
            "outfit_id": payload.get("outfit_id") or "",
        },
    )


@app.post("/tasks/execute")
def execute_task(request: TaskExecutionInput) -> dict[str, Any]:
    """Route and execute one complete StyleForge task subgraph."""
    session_context = _resolve_session_turn(request)
    try:
        payload = get_multi_task_workflow().execute(
            request, session_context=session_context
        )
    except LlmUnavailable as error:
        _append_chat_failure(request, str(error))
        raise HTTPException(
            status_code=503,
            detail=f"Three-agent task execution unavailable: {error}",
        ) from error
    except (LlmInvalidJson, LlmSchemaViolation) as error:
        _append_chat_failure(request, str(error))
        raise HTTPException(
            status_code=502,
            detail=f"Three-agent task execution returned invalid output: {error}",
        ) from error
    except ValueError as error:
        _append_chat_failure(request, str(error))
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        _append_chat_failure(request, str(error))
        raise HTTPException(
            status_code=500,
            detail=f"Task execution failed: {type(error).__name__}: {error}",
        ) from error
    message_id = _append_chat_success(request, payload)
    _record_task_event(request, payload)
    payload["session_id"] = request.session_id
    payload["message_id"] = message_id
    return payload


@app.get("/tasks/{user_id}/{run_id}")
def read_task_run(user_id: str, run_id: str) -> dict[str, Any]:
    with database_session(settings.database_dsn) as connection:
        payload = get_task_run(connection, user_id=user_id, run_id=run_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Task run not found")
    return payload


@app.get("/wardrobes/{user_id}")
def get_wardrobe(user_id: str) -> dict[str, Any]:
    with database_session(settings.database_dsn) as connection:
        rows = connection.execute(
            "SELECT c.* FROM wardrobe_items w "
            "JOIN catalog_items c ON c.item_id = w.item_id "
            "WHERE w.user_id = %s AND w.active = 1 "
            "ORDER BY c.item_type, c.item_id",
            (user_id,),
        ).fetchall()
        items = []
        for row in rows:
            item = _catalog_row_to_dict(row)
            if item["image_status"] == "available":
                try:
                    image_path = _resolve_image_path(
                        connection,
                        source=row["source"],
                        relative_image_path=row["relative_image_path"],
                        item_id=row["item_id"],
                    )
                    if not image_path.is_file():
                        item["image_status"] = "missing"
                        item["image_url"] = ""
                except HTTPException:
                    item["image_status"] = "missing"
                    item["image_url"] = ""
            items.append(item)
    return {"user_id": user_id, "count": len(items), "items": items}


@app.post("/wardrobes/{user_id}/imports", status_code=201)
def preview_wardrobe_import(
    user_id: str,
    request: OrderWorkbookUpload,
) -> dict[str, Any]:
    if not request.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=422, detail="Only .xlsx order exports are supported")
    payload = _decode_base64(request.content_base64, maximum_bytes=20 * 1024 * 1024)
    try:
        workbook = parse_order_workbook(
            payload,
            default_audience=request.default_audience,
        )
        with database_session(settings.database_dsn) as connection:
            batch_id, created, refreshed = create_import_preview(
                connection,
                user_id=user_id,
                source_filename=request.filename,
                workbook=workbook,
            )
            batch = get_import_batch(connection, user_id=user_id, batch_id=batch_id)
            rows = list_import_rows(
                connection,
                user_id=user_id,
                batch_id=batch_id,
                limit=2000,
            )
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "batch": batch,
        "created": created,
        "refreshed": refreshed,
        "count": len(rows),
        "rows": rows,
    }


@app.get("/wardrobes/{user_id}/imports/{batch_id}")
def get_wardrobe_import(user_id: str, batch_id: str) -> dict[str, Any]:
    with database_session(settings.database_dsn) as connection:
        batch = get_import_batch(connection, user_id=user_id, batch_id=batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="Wardrobe import batch not found")
        rows = list_import_rows(
            connection,
            user_id=user_id,
            batch_id=batch_id,
            limit=2000,
        )
    return {"batch": batch, "count": len(rows), "rows": rows}


@app.post("/wardrobes/{user_id}/imports/{batch_id}/commit")
def commit_wardrobe_import(
    user_id: str,
    batch_id: str,
    request: WardrobeImportCommitRequest,
) -> dict[str, Any]:
    image_root = personal_image_root(settings.artifact_root, user_id)
    image_root.mkdir(parents=True, exist_ok=True)
    try:
        with database_session(settings.database_dsn) as connection:
            item_ids = commit_import_rows(
                connection,
                user_id=user_id,
                batch_id=batch_id,
                selections=[
                    selection.model_dump(exclude_none=True)
                    for selection in request.selections
                ],
                image_root=image_root,
            )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    embedding: dict[str, Any] | None = None
    if request.auto_embed:
        try:
            embedding = embed_personal_items(
                database_path=settings.database_dsn,
                item_ids=item_ids,
                model_dir=settings.artifact_root / "models",
                device="cuda",
            )
        except BaseException as error:
            embedding = {
                "status": "failed",
                "error_type": type(error).__name__,
                "error": str(error),
                "wardrobe_commit_preserved": True,
            }
    return {
        "batch_id": batch_id,
        "user_id": user_id,
        "committed_item_count": len(item_ids),
        "item_ids": item_ids,
        "embedding": embedding,
    }


@app.post("/wardrobes/{user_id}/items/{item_id}/image")
def upload_personal_item_image(
    user_id: str,
    item_id: str,
    request: PersonalImageUpload,
) -> dict[str, Any]:
    payload = _decode_base64(request.content_base64, maximum_bytes=20 * 1024 * 1024)
    try:
        return bind_personal_image(
            database_path=settings.database_dsn,
            artifact_root=settings.artifact_root,
            user_id=user_id,
            item_id=item_id,
            image_bytes=payload,
            model_dir=settings.artifact_root / "models",
            device="cuda",
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=422, detail=f"Invalid image: {error}") from error


@app.post("/wardrobes/{user_id}/items/photo", status_code=201)
def create_wardrobe_photo_item(
    user_id: str,
    request: PhotoItemRequest,
) -> dict[str, Any]:
    payload = _decode_base64(request.content_base64, maximum_bytes=20 * 1024 * 1024)
    try:
        return create_photo_item(
            database_path=settings.database_dsn,
            artifact_root=settings.artifact_root,
            user_id=user_id,
            image_bytes=payload,
            model_dir=settings.artifact_root / "models",
            device="cuda",
            name=request.name,
            item_type=request.item_type,
            subtype=request.subtype,
            color=request.color,
            gender=request.gender,
            size=request.size,
            attributes=request.attributes,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/wardrobes/{user_id}/items/analyze")
def analyze_wardrobe_photo(
    user_id: str,
    request: PersonalImageUpload,
) -> dict[str, Any]:
    """Multi-modal recognition of one clothing photo, without persisting."""
    payload = _decode_base64(request.content_base64, maximum_bytes=20 * 1024 * 1024)
    client = vision_client_from_settings(settings)
    if client is None:
        raise HTTPException(status_code=503, detail="Vision recognition is disabled")
    try:
        raw = client.analyze_image(payload, build_analysis_prompt())
    except VisionUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except VisionInvalidJson as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=422, detail=f"Invalid image: {error}") from error
    attributes = parse_attributes(raw)
    item_type, subtype = map_ai_type_to_item_fields(
        attributes.type, attributes.subtype
    )
    return {
        "status": "available",
        "recognized": is_reliable_analysis(attributes),
        "item_type": item_type,
        "subtype": subtype,
        "color": attributes.primary_color,
        "name": attributes.description[:32] if attributes.description else "",
        "description": attributes.description,
        "confidence": attributes.confidence,
        "attributes": attributes_to_dict(attributes),
    }


BATCH_TOTAL_MAX_BYTES = 64 * 1024 * 1024


@app.post("/wardrobes/{user_id}/items/batch-recognize", status_code=202)
def start_wardrobe_photo_batch(
    user_id: str,
    request: BatchRecognitionRequest,
) -> dict[str, Any]:
    """Kick off background multi-modal recognition for many photos at once.

    Returns immediately with a ``batch_id``; the client polls
    ``GET /wardrobes/{user_id}/recognition-batches/{batch_id}`` for progress.
    Reliable recognitions are written straight into the user's wardrobe,
    failures are reported per-image so the front end can offer a manual add.
    """
    client = vision_client_from_settings(settings)
    if client is None:
        raise HTTPException(status_code=503, detail="Vision recognition is disabled")

    images: list[tuple[str, bytes]] = []
    total_bytes = 0
    for upload in request.images:
        payload = _decode_base64(
            upload.content_base64, maximum_bytes=20 * 1024 * 1024
        )
        total_bytes += len(payload)
        if total_bytes > BATCH_TOTAL_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Batch upload is too large (limit 64 MiB)",
            )
        images.append((upload.filename, payload))

    batch = start_batch(
        user_id=user_id,
        images=images,
        default_gender=request.default_gender,
        vision_client=client,
        database_path=settings.database_dsn,
        artifact_root=settings.artifact_root,
        model_dir=settings.artifact_root / "models",
    )
    return batch.snapshot()


@app.get("/wardrobes/{user_id}/recognition-batches")
def list_wardrobe_photo_batches(
    user_id: str,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """Recent recognition batches for a user, newest first (for the wardrobe page)."""
    batches = list_batches(user_id, limit)
    return {"user_id": user_id, "count": len(batches), "batches": batches}


@app.get("/wardrobes/{user_id}/recognition-batches/{batch_id}")
def get_wardrobe_photo_batch(user_id: str, batch_id: str) -> dict[str, Any]:
    """Poll progress for one batch recognition task."""
    batch = get_batch(batch_id)
    if batch is None or batch.user_id != user_id:
        raise HTTPException(status_code=404, detail="Recognition batch not found")
    return batch.snapshot()


@app.delete("/wardrobes/{user_id}/recognition-batches/{batch_id}")
def delete_wardrobe_photo_batch(user_id: str, batch_id: str) -> dict[str, Any]:
    """Remove a finished batch record once the user handled its results."""
    if not delete_batch(user_id, batch_id):
        raise HTTPException(status_code=404, detail="Recognition batch not found")
    return {"batch_id": batch_id, "deleted": True}


@app.put("/wardrobes/{user_id}/items/{item_id}")
def update_wardrobe_item(
    user_id: str,
    item_id: str,
    request: UpdateItemRequest,
) -> dict[str, Any]:
    try:
        return update_personal_item(
            database_path=settings.database_dsn,
            user_id=user_id,
            item_id=item_id,
            model_dir=settings.artifact_root / "models",
            device="cuda",
            name=request.name,
            item_type=request.item_type,
            subtype=request.subtype,
            color=request.color,
            gender=request.gender,
            size=request.size,
            attributes=request.attributes,
        )
    except ValueError as error:
        status_code = 404 if "not found" in str(error) else 422
        raise HTTPException(status_code=status_code, detail=str(error)) from error


@app.post("/wardrobes/{user_id}/items", status_code=201)
def add_wardrobe_item(user_id: str, request: WardrobeItemRequest) -> dict[str, Any]:
    with database_session(settings.database_dsn) as connection:
        row = connection.execute(
            "SELECT * FROM catalog_items WHERE item_id = %s",
            (request.item_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Catalog item not found")
        if row["image_status"] != "available":
            raise HTTPException(status_code=409, detail="Catalog item image is unavailable")
        connection.execute(
            "INSERT INTO wardrobe_items(user_id, item_id, active, favorite, notes, added_at) "
            "VALUES (%s, %s, 1, 0, '', %s) "
            "ON CONFLICT(user_id, item_id) DO UPDATE SET active = 1",
            (user_id, request.item_id, datetime.now(timezone.utc).isoformat()),
        )
    _try_behavior_event(
        user_id=user_id,
        event_type="wardrobe_adopted",
        item_id=request.item_id,
        features={"category": row["main_category"], "color": row["color"]},
    )
    return {"user_id": user_id, "item": _catalog_row_to_dict(row)}


@app.delete("/wardrobes/{user_id}/items/{item_id}")
def remove_wardrobe_item(user_id: str, item_id: str) -> dict[str, Any]:
    with database_session(settings.database_dsn) as connection:
        cursor = connection.execute(
            "UPDATE wardrobe_items SET active = 0 WHERE user_id = %s AND item_id = %s",
            (user_id, item_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Wardrobe item not found")
    _try_behavior_event(user_id=user_id, event_type="wardrobe_removed", item_id=item_id)
    return {"user_id": user_id, "item_id": item_id, "active": False}


@app.get("/catalog/search")
def search_catalog(
    q: str = Query(default="", max_length=200),
    item_type: str | None = Query(default=None, max_length=64),
    audience: str | None = Query(default=None, max_length=32),
    source: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=24, ge=1, le=100),
) -> dict[str, Any]:
    clauses = ["image_status = 'available'", "source NOT ILIKE 'personal-%'"]
    parameters: list[Any] = []
    if q.strip():
        clauses.append("(name ILIKE %s OR color ILIKE %s OR description ILIKE %s)")
        pattern = f"%{q.strip()}%"
        parameters.extend((pattern, pattern, pattern))
    if item_type:
        clauses.append("item_type = %s")
        parameters.append(item_type)
    if audience:
        clauses.append("gender = %s")
        parameters.append(audience)
    if source:
        clauses.append("source = %s")
        parameters.append(source)
    parameters.append(limit)
    sql = (
        "SELECT * FROM catalog_items WHERE "
        + " AND ".join(clauses)
        + " ORDER BY item_id LIMIT %s"
    )
    with database_session(settings.database_dsn) as connection:
        rows = connection.execute(sql, parameters).fetchall()
    return {"count": len(rows), "items": [_catalog_row_to_dict(row) for row in rows]}


@app.get("/catalog/taxonomy")
def catalog_taxonomy() -> dict[str, Any]:
    """Bilingual category tree for upload forms: main category required,
    subtype optional."""
    return {"categories": build_taxonomy()}


@app.get("/items/{item_id}")
def get_item(item_id: str) -> dict[str, Any]:
    with database_session(settings.database_dsn) as connection:
        row = connection.execute(
            "SELECT * FROM catalog_items WHERE item_id = %s",
            (item_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Catalog item not found")
    return _catalog_row_to_dict(row)


def _resolve_image_path(
    connection,
    *,
    source: str,
    relative_image_path: str,
    item_id: str | None = None,
) -> Path:
    """Resolve an image file, preferring the dataset root then the personal root.

    Uploaded and bound photos are stored under ``personal_image_root`` while
    the catalog row may still carry a dataset ``source`` (e.g. polyvore) whose
    image root is not configured. Fall back to the item owner's personal image
    root so those images remain reachable. Raises 404 when no root or file
    exists (an absent image, not a server error).
    """
    roots: list[Path] = []
    image_root = get_source_image_root(connection, source)
    if image_root is None and source == "polyvore":
        image_root = settings.image_root
    if image_root is not None:
        roots.append(image_root)
    if item_id is not None:
        user_row = connection.execute(
            "SELECT user_id FROM wardrobe_items "
            "WHERE item_id = %s AND active = 1 LIMIT 1",
            (item_id,),
        ).fetchone()
        if user_row is not None:
            roots.append(personal_image_root(settings.artifact_root, user_row["user_id"]))
    if not roots:
        raise HTTPException(status_code=404, detail="Item image not found")

    relative_path = PurePosixPath(relative_image_path)
    for root in roots:
        resolved_root = root.resolve()
        candidate = resolved_root.joinpath(*relative_path.parts).resolve()
        if not candidate.is_relative_to(resolved_root):
            raise HTTPException(status_code=400, detail="Invalid image path")
        if candidate.is_file():
            return candidate
    # No file exists under any configured root; return the first candidate so
    # the caller reports 404 instead of falling through to a missing image.
    return roots[0].resolve().joinpath(*relative_path.parts).resolve()


def _image_response(connection, row, *, item_id: str):
    image_path = _resolve_image_path(
        connection,
        source=row["source"],
        relative_image_path=row["relative_image_path"],
        item_id=item_id,
    )
    if row["image_status"] != "available" or not image_path.is_file():
        raise HTTPException(status_code=404, detail="Item image not found")
    return FileResponse(image_path, media_type="image/jpeg", filename=image_path.name)


@app.get("/items/{item_id}/image", response_class=FileResponse)
def get_item_image(item_id: str):
    with database_session(settings.database_dsn) as connection:
        row = connection.execute(
            "SELECT source, relative_image_path, image_status "
            "FROM catalog_items WHERE item_id = %s",
            (item_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Catalog item not found")
        return _image_response(connection, row, item_id=item_id)


@app.get("/items/{item_id}/images")
def list_item_images(item_id: str) -> dict[str, Any]:
    with database_session(settings.database_dsn) as connection:
        item = connection.execute(
            "SELECT source FROM catalog_items WHERE item_id = %s",
            (item_id,),
        ).fetchone()
        if item is None:
            raise HTTPException(status_code=404, detail="Catalog item not found")
        rows = connection.execute(
            "SELECT position, image_role, image_filename, relative_image_path, "
            "image_status, is_primary FROM catalog_item_images "
            "WHERE item_id = %s ORDER BY position",
            (item_id,),
        ).fetchall()
    return {
        "item_id": item_id,
        "source": item["source"],
        "count": len(rows),
        "images": [
            {
                **dict(row),
                "image_url": f"/items/{item_id}/images/{row['position']}",
            }
            for row in rows
        ],
    }


@app.get("/items/{item_id}/images/{position}", response_class=FileResponse)
def get_item_image_by_position(item_id: str, position: int):
    with database_session(settings.database_dsn) as connection:
        row = connection.execute(
            "SELECT c.source, i.relative_image_path, i.image_status "
            "FROM catalog_item_images i "
            "JOIN catalog_items c ON c.item_id = i.item_id "
            "WHERE i.item_id = %s AND i.position = %s",
            (item_id, position),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Item image not found")
        return _image_response(connection, row, item_id=item_id)


def main() -> None:
    import uvicorn

    uvicorn.run("styleforge.api:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
