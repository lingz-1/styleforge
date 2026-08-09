"""Unified Context Pack passed to every v3.3 task subgraph."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeEvidence(BaseModel):
    source: str
    source_id: str
    section: str
    content: str
    retrieved_at: str = Field(default_factory=_now)
    score: float = Field(default=0.0, ge=0.0)


class RequestContext(BaseModel):
    original_request: str
    task_type: str
    route_reason: str
    route_confidence: float


class OutfitContext(BaseModel):
    current_outfit_id: str = ""
    current_item_ids: list[str] = Field(default_factory=list)
    locked_item_ids: list[str] = Field(default_factory=list)
    editable_slots: list[str] = Field(default_factory=list)


class EnvironmentContext(BaseModel):
    weather: dict[str, Any] | None = None
    season: str | None = None


class KnowledgeContext(BaseModel):
    style_guides: list[KnowledgeEvidence] = Field(default_factory=list)
    item_guides: list[KnowledgeEvidence] = Field(default_factory=list)
    brand_guides: list[KnowledgeEvidence] = Field(default_factory=list)
    wardrobe_analysis: list[KnowledgeEvidence] = Field(default_factory=list)


class UserContext(BaseModel):
    user_id: str
    wardrobe_summary: dict[str, Any] = Field(default_factory=dict)
    preferences: dict[str, Any] = Field(default_factory=dict)
    recent_requests: list[dict[str, Any]] = Field(default_factory=list)


class ContextPack(BaseModel):
    request_context: RequestContext
    outfit_context: OutfitContext = Field(default_factory=OutfitContext)
    environment_context: EnvironmentContext = Field(default_factory=EnvironmentContext)
    knowledge_context: KnowledgeContext = Field(default_factory=KnowledgeContext)
    user_context: UserContext
    candidate_item: dict[str, Any] | None = None
    built_at: str = Field(default_factory=_now)
