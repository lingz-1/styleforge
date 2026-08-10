"""Typed state shared by the LangGraph nodes."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from styleforge.core.schemas import OutfitCandidate, RecommendationResult, TaskSpec


class WorkflowState(TypedDict, total=False):
    user_id: str
    user_query: str
    max_results: int
    task: TaskSpec
    wardrobe_item_ids: list[str]
    item_relevance: dict[str, float]
    candidates: list[OutfitCandidate]
    selected: list[OutfitCandidate]
    diagnostics: dict[str, Any]
    review_accepted: bool
    review_notes: list[str]
    result: RecommendationResult
    status: str
    error: str
    trace: Annotated[list[dict[str, Any]], operator.add]
    # Semantic v3.2.1 chain state.
    llm_enabled: bool
    llm_call_count: int
    llm_attempts: int
    fallback_count: int
    degraded_reason: str
    request_signature: dict[str, Any]
    retrieval_plans: list[dict[str, Any]]
    candidate_requirements: dict[str, int]
    retriever_degraded: bool
    pool_item_ids: list[str]
    pool_scores: dict[str, float]
    pool_quota_log: list[dict[str, Any]]
    proposals: list[dict[str, Any]]
    validated_outfits: list[dict[str, Any]]
    validation_notes: list[str]
    critic_output: dict[str, Any]
    decision: str
    best_effort: dict[str, Any]
    recent_memories: list[dict[str, Any]]
    evaluation_weights: dict[str, float]
    composer_feedback: str
    retrieval_feedback: str
    # External facts requested by Agent 1 and shared with all three agents.
    context_requirements: dict[str, Any]
    environment_context: dict[str, Any]
    tool_calls: list[dict[str, Any]]
    context_router_completed: bool
    # V2.1 weather context: optional device location from the request, the
    # user's default city/timezone, and the resolved (privacy-safe) contexts.
    location_context: dict[str, Any]
    environment_profile: dict[str, Any]
    resolved_location_context: dict[str, Any]
    resolved_time_context: dict[str, Any]
