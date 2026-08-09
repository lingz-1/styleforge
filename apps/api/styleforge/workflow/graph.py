"""LangGraph workflow connecting the semantic v3.2.1 agents and the
deterministic fallback chain.

A single graph hosts both paths. When an LLM client is available the request
flows through the three semantic agents; a degraded semantic retriever jumps
to the deterministic chain. The critic routes by one of the four frozen
decisions via ``Command``, with at most one recompose/retrieve_more loop.
"""

from __future__ import annotations

import argparse
import json
import threading
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from styleforge.agents import (
    ComposerAgent,
    CriticAgent,
    PlannerAgent,
    ReviewerAgent,
    SemanticRetrieverAgent,
    StylistAgent,
)
from styleforge.agents.critic import deterministic_critic
from styleforge.common.console import configure_utf8_console
from styleforge.core.categories import infer_slot
from styleforge.core.config import Settings
from styleforge.core.rubric import normalize_weights
from styleforge.core.schemas import (
    CatalogItem,
    EmbeddingStatus,
    ImageStatus,
    RecommendationResult,
    TaskSpec,
)
from styleforge.core.scoring import score_outfit
from styleforge.core.slots import base_slot
from styleforge.llm.client import llm_client_from_settings
from styleforge.repositories.catalog_repository import fetch_items_by_ids
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.personal_embedding_repository import PersonalEmbeddingStore
from styleforge.repositories.run_repository import (
    fail_run,
    finish_run,
    save_candidates,
    save_semantic_detail,
    start_run,
)
from styleforge.repositories.wardrobe_repository import list_items
from styleforge.services.catalog_vector_store import CatalogVectorStore, normalize_relevance
from styleforge.services.presentation import present_result
from styleforge.services.semantic_retrieval import (
    novelty_scores,
    preference_scores,
    score_multi_query,
)
from styleforge.tools.basic_validation import proposal_to_candidate, validate_proposals
from styleforge.tools.candidate_generation import generate_candidates
from styleforge.tools.candidate_pool import PoolOutcome, build_candidate_pool, pool_manifest
from styleforge.vision.fashion_clip import FashionClipEncoder
from styleforge.workflow.state import WorkflowState


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trace(node: str, **details: Any) -> list[dict[str, Any]]:
    return [{"node": node, "timestamp": _now(), **details}]


class _TranscriptClient:
    """Wraps an LLM client to record full prompt transcripts (verbose mode)."""

    def __init__(self, inner: Any, transcripts: list[dict[str, Any]]) -> None:
        self._inner = inner
        self._transcripts = transcripts

    def chat_json(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict[str, Any],
        temperature: float = 0.2,
    ) -> tuple[dict[str, Any], Any]:
        payload, diagnostics = self._inner.chat_json(
            system=system,
            user=user,
            json_schema=json_schema,
            temperature=temperature,
        )
        self._transcripts.append({"system": system, "user": user, "parsed": payload})
        return payload, diagnostics


def _row_to_catalog_item(row) -> CatalogItem:
    return CatalogItem(
        item_id=row["item_id"],
        source=row["source"],
        gender=row["gender"],
        item_type=row["item_type"],
        main_category=row["main_category"],
        name=row["name"],
        color=row["color"],
        description=row["description"],
        features=tuple(json.loads(row["features_json"])),
        image_filename=row["image_filename"],
        relative_image_path=row["relative_image_path"],
        image_status=ImageStatus(row["image_status"]),
        embedding_status=EmbeddingStatus(row["embedding_status"]),
        raw_json_hash=row["raw_json_hash"],
    )


def _wardrobe_summary(items: list[CatalogItem]) -> dict[str, Any]:
    by_slot: dict[str, list[CatalogItem]] = defaultdict(list)
    for item in items:
        by_slot[base_slot(infer_slot(item.item_type))].append(item)
    summary: dict[str, Any] = {}
    for slot, slot_items in sorted(by_slot.items()):
        summary[slot] = {
            "count": len(slot_items),
            "sample_types": sorted({item.item_type for item in slot_items})[:8],
            "sample_colors": sorted({item.color for item in slot_items if item.color})[:8],
        }
    return summary


def _build_by_slot(items: list[CatalogItem], task: TaskSpec) -> dict[str, list[str]]:
    by_slot: dict[str, list[str]] = defaultdict(list)
    for item in items:
        if task.target_audiences and item.gender not in task.target_audiences:
            continue
        by_slot[base_slot(infer_slot(item.item_type))].append(item.item_id)
    return dict(by_slot)


def _critic_dimension_scores(
    critic_output: dict[str, Any] | None,
    outfit_id: str,
) -> dict[str, Any] | None:
    """Return the critic's five-dimension scores for an outfit, if scored.

    Both the preferred ``outfit_assessment`` and every ``alternatives`` entry
    may carry dimension scores; this resolves whichever matches the outfit.
    """
    if not critic_output:
        return None
    assessment = critic_output.get("outfit_assessment", {})
    if assessment.get("outfit_id") == outfit_id and assessment.get("dimension_scores"):
        return assessment["dimension_scores"]
    for alternative in critic_output.get("alternatives", []):
        if (
            alternative.get("outfit_id") == outfit_id
            and alternative.get("dimension_scores")
        ):
            return alternative["dimension_scores"]
    return None


def _critic_score(
    critic_output: dict[str, Any] | None,
    outfit_id: str,
    weights: dict[str, float] | None = None,
) -> float:
    """Weighted LLM five-dimension score on a 0-100 scale (0 when unscored)."""
    dimensions = _critic_dimension_scores(critic_output, outfit_id)
    if not dimensions:
        return 0.0
    resolved = normalize_weights(weights)
    weighted = (
        resolved["request_relevance"] * float(dimensions.get("request_relevance", 5))
        + resolved["request_specificity"]
        * float(dimensions.get("request_specificity", 5))
        + resolved["outfit_coordination"]
        * float(dimensions.get("outfit_coordination", 5))
        + resolved["wearability"] * float(dimensions.get("wearability", 5))
        + resolved["freshness"] * float(dimensions.get("freshness", 5))
    )
    return round(weighted * 10, 2)


def _llm_score(
    critic_output: dict[str, Any] | None,
    outfit_id: str,
    weights: dict[str, float] | None = None,
) -> float | None:
    """Weighted LLM score, or None when the critic did not score this outfit."""
    if _critic_dimension_scores(critic_output, outfit_id) is None:
        return None
    return _critic_score(critic_output, outfit_id, weights)


def _rule_score(
    proposal: dict[str, Any],
    items_by_id: dict[str, CatalogItem],
    task: TaskSpec,
) -> float:
    """Deterministic rubric score on a 0-100 scale."""
    item_list = [
        items_by_id[item_id]
        for item_id in proposal.get("item_ids", [])
        if item_id in items_by_id
    ]
    if not item_list:
        return 0.0
    score, _, _ = score_outfit(item_list, task)
    return round(score, 2)


@dataclass(frozen=True, slots=True)
class WorkflowOutput:
    mode: str
    request: str
    task: TaskSpec
    result: RecommendationResult
    review_notes: tuple[str, ...]
    diagnostics: dict[str, Any]
    trace: tuple[dict[str, Any], ...]
    llm_enabled: bool = False
    llm_call_count: int = 0
    llm_attempts: int = 0
    fallback_count: int = 0
    degraded_reason: str = ""
    request_signature: dict[str, Any] | None = None
    retrieval_plans: list[dict[str, Any]] | None = None
    candidate_requirements: dict[str, int] | None = None
    pool: dict[str, Any] | None = None
    proposals: list[dict[str, Any]] | None = None
    validation: dict[str, Any] | None = None
    critic: dict[str, Any] | None = None
    decision: str = ""
    best_effort: dict[str, Any] | None = None


class StyleForgeWorkflow:
    """Offline-first multi-agent workflow with graceful LLM/vision fallback."""

    def __init__(
        self,
        *,
        database_path: Path,
        embedding_dir: Path,
        model_dir: Path,
        device: str = "cuda",
        llm_client: Any | None = None,
        settings: Settings | None = None,
        llm_verbose: bool = False,
    ) -> None:
        self.database_path = database_path.resolve()
        self.embedding_dir = embedding_dir.resolve()
        self.model_dir = model_dir.resolve()
        self.device = device
        if settings is None:
            settings = Settings.from_env()
        self.settings = settings
        if llm_client is None and settings.llm_enabled:
            llm_client = llm_client_from_settings(settings)
        self.llm_verbose = llm_verbose
        self._llm_transcripts: list[dict[str, Any]] = []
        self.llm_enabled = llm_client is not None
        if llm_client is not None and llm_verbose:
            llm_client = _TranscriptClient(llm_client, self._llm_transcripts)
        self._llm_client = llm_client
        self.planner = PlannerAgent()
        self.stylist = StylistAgent()
        self.reviewer = ReviewerAgent()
        self.semantic_retriever = SemanticRetrieverAgent()
        self.composer = ComposerAgent()
        self.critic = CriticAgent()
        self._encoder: FashionClipEncoder | None = None
        self._vector_store: CatalogVectorStore | None = None
        self._encoder_error: str | None = None
        self._vector_store_error: str | None = None
        self._vector_store_attempted = False
        self._vision_lock = threading.Lock()
        initialize_database(self.database_path)
        self.graph = self._build_graph()

    # --- routing ----------------------------------------------------------

    def _route_entry(self, state: WorkflowState) -> str:
        return "semantic_retriever" if state.get("llm_enabled") else "slot_retriever"

    def _route_after_retriever(self, state: WorkflowState) -> str:
        return "slot_retriever" if state.get("retriever_degraded") else "multi_query_retrieval"

    def _build_graph(self):
        builder = StateGraph(WorkflowState)
        builder.add_node("planner_agent", self._planner_node)
        builder.add_node("slot_retriever", self._retrieval_node)
        builder.add_node("candidate_generator", self._candidate_node)
        builder.add_node("stylist_agent", self._stylist_node)
        builder.add_node("reviewer_agent", self._reviewer_node)
        builder.add_node("persist_result", self._persist_node)
        builder.add_node("semantic_retriever", self._semantic_retriever_node)
        builder.add_node("multi_query_retrieval", self._multi_query_retrieval_node)
        builder.add_node("candidate_pool", self._candidate_pool_node)
        builder.add_node("composer", self._composer_node)
        builder.add_node("basic_validation", self._basic_validation_node)
        builder.add_node("critic", self._critic_node)
        builder.add_node("best_effort", self._best_effort_node)

        builder.add_edge(START, "planner_agent")
        builder.add_conditional_edges(
            "planner_agent",
            self._route_entry,
            {"semantic_retriever": "semantic_retriever", "slot_retriever": "slot_retriever"},
        )
        builder.add_conditional_edges(
            "semantic_retriever",
            self._route_after_retriever,
            {"multi_query_retrieval": "multi_query_retrieval", "slot_retriever": "slot_retriever"},
        )
        builder.add_edge("multi_query_retrieval", "candidate_pool")
        builder.add_edge("candidate_pool", "composer")
        builder.add_edge("composer", "basic_validation")
        builder.add_edge("basic_validation", "critic")
        builder.add_edge("best_effort", "persist_result")
        builder.add_edge("slot_retriever", "candidate_generator")
        builder.add_edge("candidate_generator", "stylist_agent")
        builder.add_edge("stylist_agent", "reviewer_agent")
        builder.add_edge("reviewer_agent", "persist_result")
        builder.add_edge("persist_result", END)
        return builder.compile()

    # --- deterministic nodes (kept for the fallback chain) -----------------

    def _planner_node(self, state: WorkflowState) -> dict[str, Any]:
        task = self.planner.plan(
            state["user_id"],
            state["user_query"],
            state.get("max_results", 3),
        )
        return {
            "task": task,
            "status": "planned",
            "trace": _trace(
                "planner_agent",
                backend=self.planner.backend,
                occasion=task.occasion,
                target_audiences=list(task.target_audiences),
                required_slots=list(task.required_slots),
            ),
        }

    def _ensure_vision(self) -> tuple[FashionClipEncoder, CatalogVectorStore | None]:
        if self._encoder_error is not None:
            raise RuntimeError(self._encoder_error)
        if self._encoder is None:
            try:
                self._encoder = FashionClipEncoder(
                    self.model_dir,
                    device=self.device,
                    precision="float16",
                )
            except BaseException as error:
                self._encoder_error = f"{type(error).__name__}: {error}"
                raise
        if not self._vector_store_attempted:
            self._vector_store_attempted = True
            try:
                self._vector_store = CatalogVectorStore(self.embedding_dir)
            except BaseException as error:
                self._vector_store_error = f"{type(error).__name__}: {error}"
        return self._encoder, self._vector_store

    def _retrieval_node(self, state: WorkflowState) -> dict[str, Any]:
        task = state["task"]
        with database_session(self.database_path) as connection:
            wardrobe_items = list_items(connection, task.user_id)
        wardrobe_ids = [item.item_id for item in wardrobe_items]
        audience_items = [
            item
            for item in wardrobe_items
            if not task.target_audiences or item.gender in task.target_audiences
        ]
        by_slot: dict[str, list[str]] = {}
        for item in audience_items:
            by_slot.setdefault(infer_slot(item.item_type), []).append(item.item_id)

        relevance: dict[str, float] = {}
        retrieval_diagnostics: dict[str, Any] = {
            "vision_available": False,
            "wardrobe_item_count": len(wardrobe_items),
            "audience_candidate_count": len(audience_items),
            "target_audiences": list(task.target_audiences),
            "slot_candidate_counts": {
                slot: len(item_ids) for slot, item_ids in sorted(by_slot.items())
            },
        }
        if wardrobe_items:
            try:
                with self._vision_lock:
                    encoder, vector_store = self._ensure_vision()
                    prompts = [
                        self.planner.retrieval_prompt(task, slot)
                        for slot in task.required_slots
                    ]
                    query_vectors = encoder.encode_texts(prompts)
                    with database_session(self.database_path) as embedding_connection:
                        personal_store = PersonalEmbeddingStore(embedding_connection)
                        personal_score_count = 0
                        for slot, prompt, query_vector in zip(
                            task.required_slots,
                            prompts,
                            query_vectors,
                            strict=True,
                        ):
                            allowed_ids = by_slot.get(base_slot(slot), [])
                            slot_scores = (
                                vector_store.score_items(query_vector, allowed_ids)
                                if vector_store is not None
                                else {}
                            )
                            personal_scores = personal_store.score_items(
                                query_vector, allowed_ids
                            )
                            slot_scores.update(personal_scores)
                            personal_score_count += len(personal_scores)
                            relevance.update(normalize_relevance(slot_scores))
                            retrieval_diagnostics.setdefault("prompts", {})[slot] = prompt
                retrieval_diagnostics["vision_available"] = True
                retrieval_diagnostics["personal_embedding_scores"] = personal_score_count
                if vector_store is not None:
                    retrieval_diagnostics["embedding_version"] = vector_store.manifest.get(
                        "embedding_version"
                    )
                elif self._vector_store_error:
                    retrieval_diagnostics["catalog_vector_fallback_reason"] = (
                        self._vector_store_error
                    )
            except BaseException as error:
                retrieval_diagnostics["fallback_reason"] = f"{type(error).__name__}: {error}"

        return {
            "wardrobe_item_ids": wardrobe_ids,
            "item_relevance": relevance,
            "diagnostics": {"retrieval": retrieval_diagnostics},
            "status": "retrieved",
            "trace": _trace(
                "slot_retriever",
                vision_available=retrieval_diagnostics["vision_available"],
                wardrobe_item_count=len(wardrobe_items),
            ),
        }

    def _candidate_node(self, state: WorkflowState) -> dict[str, Any]:
        task = state["task"]
        with database_session(self.database_path) as connection:
            wardrobe_items = list_items(connection, task.user_id)
        candidates, generation_diagnostics = generate_candidates(
            wardrobe_items,
            task,
            per_slot_limit=12,
            max_candidates=2000,
            item_relevance=state.get("item_relevance"),
        )
        diagnostics = dict(state.get("diagnostics", {}))
        diagnostics["generation"] = generation_diagnostics
        return {
            "candidates": candidates,
            "diagnostics": diagnostics,
            "status": "candidates_generated",
            "trace": _trace(
                "candidate_generator",
                candidate_count=len(candidates),
                missing_slots=generation_diagnostics.get("missing_slots", []),
            ),
        }

    def _stylist_node(self, state: WorkflowState) -> dict[str, Any]:
        selected = self.stylist.select(state.get("candidates", []), state["task"])
        return {
            "selected": selected,
            "status": "styled",
            "trace": _trace(
                "stylist_agent",
                backend=self.stylist.backend,
                selected_count=len(selected),
                selected_outfit_ids=[candidate.outfit_id for candidate in selected],
            ),
        }

    def _reviewer_node(self, state: WorkflowState) -> dict[str, Any]:
        decision = self.reviewer.review(
            state.get("selected", []),
            state["task"],
            set(state.get("wardrobe_item_ids", [])),
        )
        return {
            "review_accepted": decision.accepted,
            "review_notes": list(decision.notes),
            "status": "accepted" if decision.accepted else "infeasible",
            "trace": _trace(
                "reviewer_agent",
                backend=self.reviewer.backend,
                accepted=decision.accepted,
                notes=list(decision.notes),
            ),
        }

    # --- semantic nodes -----------------------------------------------------

    def _semantic_retriever_node(self, state: WorkflowState) -> dict[str, Any]:
        task = state["task"]
        with database_session(self.database_path) as connection:
            wardrobe_items = list_items(connection, task.user_id)
        summary = _wardrobe_summary(wardrobe_items)
        user_query = state["user_query"]
        feedback = state.get("retrieval_feedback", "")
        if feedback:
            user_query = f"{user_query}\n（上一轮评审反馈：{feedback}）"
        try:
            output, info, _ = self.semantic_retriever.run(
                user_query=user_query,
                task=task,
                wardrobe_summary=summary,
                recent_memories=state.get("recent_memories", []),
                llm=self._llm_client,
                weights=state.get("evaluation_weights"),
            )
        except BaseException as error:
            output, info, _ = self.semantic_retriever.run(
                user_query=user_query,
                task=task,
                wardrobe_summary=summary,
                recent_memories=state.get("recent_memories", []),
                llm=None,
                weights=state.get("evaluation_weights"),
            )
            info["reason"] = f"{type(error).__name__}: {error}"
        return {
            "request_signature": output.to_dict()["request_signature"],
            "retrieval_plans": output.to_dict()["retrieval_plans"],
            "candidate_requirements": output.to_dict()["candidate_requirements"],
            "retriever_degraded": info["degraded"],
            "degraded_reason": info.get("reason", ""),
            "llm_call_count": state.get("llm_call_count", 0) + (0 if info["degraded"] else 1),
            "llm_attempts": state.get("llm_attempts", 0) + 1,
            "status": "signature_ready",
            "trace": _trace(
                "semantic_retriever",
                backend=self.semantic_retriever.backend,
                degraded=info["degraded"],
            ),
        }

    def _multi_query_retrieval_node(self, state: WorkflowState) -> dict[str, Any]:
        task = state["task"]
        plans = state.get("retrieval_plans", [])
        with database_session(self.database_path) as connection:
            wardrobe_items = list_items(connection, task.user_id)
        by_slot = _build_by_slot(wardrobe_items, task)
        preference = preference_scores(wardrobe_items, task)
        novelty = novelty_scores(wardrobe_items, state.get("recent_memories", []))
        try:
            with self._vision_lock:
                encoder, vector_store = self._ensure_vision()
                with database_session(self.database_path) as connection:
                    personal_store = PersonalEmbeddingStore(connection)
                    outcome = score_multi_query(
                        plans=plans,
                        wardrobe_items=wardrobe_items,
                        by_slot=by_slot,
                        encoder=encoder,
                        catalog_store=vector_store,
                        personal_store=personal_store,
                        preference=preference,
                        novelty=novelty,
                    )
            final_scores = outcome.final_scores
            retrieval_diagnostics: dict[str, Any] = dict(outcome.diagnostics)
            retrieval_diagnostics["vision_available"] = True
            if vector_store is not None:
                retrieval_diagnostics["embedding_version"] = vector_store.manifest.get(
                    "embedding_version"
                )
        except BaseException as error:
            final_scores = {}
            retrieval_diagnostics = {
                "vision_available": False,
                "fallback_reason": f"{type(error).__name__}: {error}",
            }
        return {
            "wardrobe_item_ids": [item.item_id for item in wardrobe_items],
            "pool_scores": final_scores,
            "diagnostics": {"retrieval": retrieval_diagnostics},
            "status": "retrieved",
            "trace": _trace(
                "multi_query_retrieval",
                vision_available=retrieval_diagnostics.get("vision_available", False),
            ),
        }

    def _candidate_pool_node(self, state: WorkflowState) -> dict[str, Any]:
        task = state["task"]
        with database_session(self.database_path) as connection:
            wardrobe_items = list_items(connection, task.user_id)
        outcome = build_candidate_pool(
            wardrobe_items=wardrobe_items,
            final_scores=state.get("pool_scores", {}),
            requirements=state.get("candidate_requirements", {}),
            task=task,
        )
        return {
            "pool_item_ids": [item.item_id for item in outcome.pool_items],
            "pool_scores": outcome.pool_scores,
            "pool_quota_log": outcome.quota_transfer_log,
            "diagnostics": {"pool": outcome.diagnostics},
            "status": "pool_ready",
            "trace": _trace(
                "candidate_pool",
                pool_size=len(outcome.pool_items),
                transfer_count=len(outcome.quota_transfer_log),
            ),
        }

    def _composer_node(self, state: WorkflowState) -> dict[str, Any]:
        task = state["task"]
        pool_item_ids = state.get("pool_item_ids", [])
        with database_session(self.database_path) as connection:
            rows = fetch_items_by_ids(connection, pool_item_ids)
        pool_items = [_row_to_catalog_item(row) for row in rows]
        pool_scores = state.get("pool_scores", {})
        pool_ids = {item.item_id for item in pool_items}
        manifest = pool_manifest(
            PoolOutcome(
                pool_items=pool_items,
                pool_scores=pool_scores,
                quota_used={},
                quota_transfer_log=[],
                diagnostics={},
            )
        )
        user_query = state["user_query"]
        feedback = state.get("composer_feedback", "")
        if feedback:
            user_query = f"{user_query}\n（上一轮评审反馈：{feedback}）"
        recent_structure_signatures = [
            memory.get("structure_signature", {})
            for memory in state.get("recent_memories", [])
            if memory.get("structure_signature")
        ]
        try:
            proposals, info, _ = self.composer.run(
                user_query=user_query,
                request_signature=state.get("request_signature", {}),
                pool_manifest=manifest,
                recent_structure_signatures=recent_structure_signatures,
                llm=self._llm_client,
                pool_ids=pool_ids,
                task=task,
                pool_items=pool_items,
                pool_scores=pool_scores,
                weights=state.get("evaluation_weights"),
            )
        except BaseException as error:
            proposals, info, _ = self.composer.run(
                user_query=user_query,
                request_signature=state.get("request_signature", {}),
                pool_manifest=manifest,
                recent_structure_signatures=recent_structure_signatures,
                llm=None,
                pool_ids=pool_ids,
                task=task,
                pool_items=pool_items,
                pool_scores=pool_scores,
                weights=state.get("evaluation_weights"),
            )
            info["reason"] = f"{type(error).__name__}: {error}"
        proposal_dicts = [proposal.to_dict() for proposal in proposals]
        return {
            "proposals": proposal_dicts,
            "llm_call_count": state.get("llm_call_count", 0) + (0 if info["degraded"] else 1),
            "llm_attempts": state.get("llm_attempts", 0) + 1,
            "status": "composed",
            "diagnostics": {
                "composer": info.get("generation_diagnostics", info.get("diagnostics", {}))
            },
            "trace": _trace(
                "composer",
                backend=self.composer.backend,
                degraded=info["degraded"],
                proposal_count=len(proposal_dicts),
            ),
        }

    def _basic_validation_node(self, state: WorkflowState) -> dict[str, Any]:
        proposals = state.get("proposals", [])
        pool_item_ids = state.get("pool_item_ids", [])
        with database_session(self.database_path) as connection:
            rows = fetch_items_by_ids(connection, pool_item_ids)
        available = [_row_to_catalog_item(row) for row in rows]
        validated, notes = validate_proposals(proposals, available)
        return {
            "validated_outfits": validated,
            "validation_notes": notes,
            "status": "validated",
            "trace": _trace(
                "basic_validation",
                proposed=len(proposals),
                validated=len(validated),
                dropped=len(proposals) - len(validated),
            ),
        }

    def _critic_node(self, state: WorkflowState) -> Command:
        validated = state.get("validated_outfits", [])
        user_query = state["user_query"]
        request_signature = state.get("request_signature", {})
        try:
            critic_output, info, _ = self.critic.run(
                user_query=user_query,
                request_signature=request_signature,
                outfits=validated,
                llm=self._llm_client,
                task=state["task"],
                wardrobe_ids=set(state.get("wardrobe_item_ids", [])),
                weights=state.get("evaluation_weights"),
            )
        except BaseException as error:
            critic_output = deterministic_critic(
                validated,
                state["task"],
                set(state.get("wardrobe_item_ids", [])),
            )
            info = {"degraded": True, "reason": f"{type(error).__name__}: {error}"}
        decision = critic_output.decision
        update: dict[str, Any] = {
            "critic_output": critic_output.to_dict(),
            "decision": decision,
            "llm_call_count": state.get("llm_call_count", 0) + (0 if info["degraded"] else 1),
            "llm_attempts": state.get("llm_attempts", 0) + 1,
            "status": "critiqued",
            "trace": _trace(
                "critic_agent",
                backend=self.critic.backend,
                degraded=info["degraded"],
                decision=decision,
            ),
        }
        if decision == "accept":
            update["status"] = "accepted"
            return Command(goto="persist_result", update=update)
        if decision == "recompose":
            if state.get("fallback_count", 0) < 1 and state.get("llm_call_count", 0) <= 3:
                update["fallback_count"] = state.get("fallback_count", 0) + 1
                update["composer_feedback"] = critic_output.feedback
                update["status"] = "recomposing"
                return Command(goto="composer", update=update)
            return Command(goto="best_effort", update=update)
        if decision == "retrieve_more":
            if state.get("fallback_count", 0) < 1 and state.get("llm_call_count", 0) <= 3:
                update["fallback_count"] = state.get("fallback_count", 0) + 1
                update["retrieval_feedback"] = critic_output.feedback
                update["status"] = "retrieving_more"
                return Command(goto="semantic_retriever", update=update)
            return Command(goto="best_effort", update=update)
        # wardrobe_gap
        update["status"] = "wardrobe_gap"
        return Command(goto="best_effort", update=update)

    def _best_effort_node(self, state: WorkflowState) -> dict[str, Any]:
        critic_output = state.get("critic_output", {})
        decision = state.get("decision", "wardrobe_gap")
        best_effort = {
            "decision": decision,
            "outfits": state.get("validated_outfits", []),
            "missing_items": critic_output.get("missing_items", []),
            "feedback": critic_output.get("feedback", ""),
        }
        return {
            "best_effort": best_effort,
            "status": "best_effort",
            "trace": _trace(
                "best_effort",
                decision=decision,
                outfit_count=len(best_effort["outfits"]),
                missing_count=len(best_effort["missing_items"]),
            ),
        }

    # --- persistence ---------------------------------------------------------

    def _select_semantic_outfits(self, state: WorkflowState) -> list[dict[str, Any]]:
        if state.get("decision") == "accept":
            proposals = state.get("validated_outfits", [])
        elif state.get("best_effort"):
            proposals = state.get("best_effort", {}).get("outfits", [])
        else:
            proposals = state.get("validated_outfits", [])
        preferred_id = (
            state.get("critic_output", {})
            .get("outfit_assessment", {})
            .get("outfit_id", "")
        )
        if preferred_id:
            proposals = sorted(
                proposals,
                key=lambda proposal: (proposal.get("outfit_id") != preferred_id, 0),
            )
        return proposals

    def _persist_node(self, state: WorkflowState) -> dict[str, Any]:
        task = state["task"]
        if state.get("llm_enabled") and (
            state.get("decision") is not None or state.get("validated_outfits") is not None
        ):
            return self._persist_semantic(state, task)
        return self._persist_deterministic(state, task)

    def _persist_semantic(self, state: WorkflowState, task: TaskSpec) -> dict[str, Any]:
        proposals = self._select_semantic_outfits(state)
        wardrobe_ids = set(state.get("wardrobe_item_ids", []))
        item_ids = [
            item_id
            for proposal in proposals
            for item_id in proposal.get("item_ids", [])
        ]
        with database_session(self.database_path) as connection:
            rows = fetch_items_by_ids(connection, item_ids)
            run_id = start_run(connection, task)
        items_by_id = {row["item_id"]: _row_to_catalog_item(row) for row in rows}
        critic_output = state.get("critic_output")
        weights = state.get("evaluation_weights")
        scored: list = []
        for proposal in proposals:
            proposal_ids = set(proposal.get("item_ids", []))
            if not proposal_ids or not proposal_ids <= wardrobe_ids:
                continue
            llm_score = _llm_score(critic_output, proposal.get("outfit_id", ""), weights)
            rule_score = _rule_score(proposal, items_by_id, task)
            sort_score = llm_score if llm_score is not None else rule_score
            scored.append((sort_score, proposal, llm_score, rule_score))
        # Rank by LLM score descending; rule score breaks ties.
        scored.sort(key=lambda entry: (entry[0], entry[3]), reverse=True)
        candidates = [
            proposal_to_candidate(
                proposal,
                items_by_id,
                sort_score,
                llm_score=llm_score,
                rule_score=rule_score,
            )
            for sort_score, proposal, llm_score, rule_score in scored
        ]
        status = "completed" if candidates else "infeasible"
        notes = list(state.get("validation_notes", []))
        if state.get("decision") != "accept" and state.get("best_effort", {}).get("feedback"):
            notes.append(state["best_effort"]["feedback"])
        result = RecommendationResult(
            run_id=run_id,
            status=status,
            recommendations=tuple(candidates),
            diagnostics=state.get("diagnostics", {}),
        )
        semantic_detail: dict[str, object] = {
            "request_signature": state.get("request_signature"),
            "retrieval_plans": state.get("retrieval_plans"),
            "candidate_requirements": state.get("candidate_requirements"),
            "pool": {
                "item_ids": state.get("pool_item_ids"),
                "scores": state.get("pool_scores"),
                "quota_transfer_log": state.get("pool_quota_log"),
            },
            "proposals": state.get("proposals"),
            "validation": {"notes": state.get("validation_notes")},
            "critic": state.get("critic_output"),
            "decision": state.get("decision"),
            "fallback_count": state.get("fallback_count"),
            "llm_call_count": state.get("llm_call_count"),
            "llm_attempts": state.get("llm_attempts"),
            "llm_verbose": self.llm_verbose,
            "llm_transcripts": list(self._llm_transcripts),
        }
        with database_session(self.database_path) as connection:
            save_candidates(connection, run_id, candidates)
            save_semantic_detail(connection, run_id, semantic_detail)
            finish_run(connection, result)
            self._save_request_memory(connection, state, task.user_id, candidates)
        return {
            "result": result,
            "status": result.status,
            "review_notes": notes,
            "trace": _trace("persist_result", run_id=run_id, status=result.status),
        }

    def _save_request_memory(
        self,
        connection,
        state: WorkflowState,
        user_id: str,
        candidates: list,
    ) -> None:
        """Persist the request signature and best outfit's structure signature."""
        from styleforge.repositories.request_memory_repository import save_request_memory

        request_signature = state.get("request_signature")
        if not request_signature:
            return
        structure_signature = None
        if candidates:
            from styleforge.core.structure_signature import structure_signature as build_signature

            with database_session(self.database_path) as item_connection:
                rows = fetch_items_by_ids(item_connection, candidates[0].item_ids)
            items = [_row_to_catalog_item(row) for row in rows]
            if items:
                structure_signature = build_signature(items).to_dict()
        save_request_memory(
            connection,
            user_id=user_id,
            request_signature=request_signature,
            structure_signature=structure_signature,
        )

    def _persist_deterministic(self, state: WorkflowState, task: TaskSpec) -> dict[str, Any]:
        selected = state.get("selected", []) if state.get("review_accepted") else []
        with database_session(self.database_path) as connection:
            run_id = start_run(connection, task)
        result = RecommendationResult(
            run_id=run_id,
            status="completed" if state.get("review_accepted") else "infeasible",
            recommendations=tuple(selected),
            diagnostics=state.get("diagnostics", {}),
        )
        with database_session(self.database_path) as connection:
            save_candidates(connection, run_id, selected)
            finish_run(connection, result)
        return {
            "result": result,
            "status": result.status,
            "review_notes": state.get("review_notes", []),
            "trace": _trace("persist_result", run_id=run_id, status=result.status),
        }

    # --- entry points ---------------------------------------------------------

    def _load_recent_memories(self, user_id: str) -> list[dict[str, Any]]:
        try:
            from styleforge.repositories.request_memory_repository import recent_request_memories

            with database_session(self.database_path) as connection:
                return recent_request_memories(connection, user_id, limit=5)
        except BaseException:
            return []

    def _load_evaluation_weights(self, user_id: str) -> dict[str, float]:
        try:
            from styleforge.repositories.user_preferences_repository import get_evaluation_weights

            with database_session(self.database_path) as connection:
                return get_evaluation_weights(connection, user_id)
        except BaseException:
            from styleforge.core.rubric import DEFAULT_EVALUATION_WEIGHTS

            return dict(DEFAULT_EVALUATION_WEIGHTS)

    def recommend(
        self,
        *,
        user_id: str,
        request: str,
        max_results: int = 3,
    ) -> WorkflowOutput:
        initial: WorkflowState = {
            "user_id": user_id,
            "user_query": request,
            "max_results": max_results,
            "status": "running",
            "trace": [],
            "llm_enabled": self.llm_enabled,
            "llm_call_count": 0,
            "llm_attempts": 0,
            "fallback_count": 0,
            "recent_memories": self._load_recent_memories(user_id),
            "evaluation_weights": self._load_evaluation_weights(user_id),
            "retriever_degraded": False,
        }
        try:
            final = self.graph.invoke(initial)
        except BaseException as error:
            task = self.planner.plan(user_id, request, max_results)
            with database_session(self.database_path) as connection:
                run_id = start_run(connection, task)
                fail_run(connection, run_id, error)
            raise
        semantic = final.get("decision") is not None
        mode = (
            "llm_semantic_multi_agent"
            if self.llm_enabled and semantic
            else "deterministic_multi_agent_with_fashionclip"
        )
        return WorkflowOutput(
            mode=mode,
            request=request,
            task=final["task"],
            result=final["result"],
            review_notes=tuple(final.get("review_notes", [])),
            diagnostics=final.get("diagnostics", {}),
            trace=tuple(final.get("trace", [])),
            llm_enabled=self.llm_enabled,
            llm_call_count=final.get("llm_call_count", 0),
            llm_attempts=final.get("llm_attempts", 0),
            fallback_count=final.get("fallback_count", 0),
            degraded_reason=final.get("degraded_reason", ""),
            request_signature=final.get("request_signature"),
            retrieval_plans=final.get("retrieval_plans"),
            candidate_requirements=final.get("candidate_requirements"),
            pool=self._build_pool_payload(final),
            proposals=final.get("proposals"),
            validation=self._build_validation_payload(final),
            critic=final.get("critic_output"),
            decision=final.get("decision", ""),
            best_effort=final.get("best_effort"),
        )

    def _build_pool_payload(self, final: WorkflowState) -> dict[str, Any] | None:
        pool_item_ids = final.get("pool_item_ids")
        if pool_item_ids is None:
            return None
        return {
            "item_ids": list(pool_item_ids),
            "total": len(pool_item_ids),
            "quota_transfer_log": final.get("pool_quota_log", []),
        }

    def _build_validation_payload(self, final: WorkflowState) -> dict[str, Any] | None:
        validated = final.get("validated_outfits")
        if validated is None:
            return None
        proposals = final.get("proposals", [])
        return {
            "notes": final.get("validation_notes", []),
            "valid_outfit_ids": [proposal.get("outfit_id", "") for proposal in validated],
            "dropped_outfit_ids": [
                proposal.get("outfit_id", "")
                for proposal in proposals
                if proposal not in validated
            ],
        }

    def recommend_payload(
        self,
        *,
        user_id: str,
        request: str,
        max_results: int = 3,
    ) -> dict[str, Any]:
        output = self.recommend(
            user_id=user_id,
            request=request,
            max_results=max_results,
        )
        presented_result = present_result(self.database_path, output.result)
        payload: dict[str, Any] = {
            "mode": output.mode,
            "request": output.request,
            "parsed_task": output.task.to_dict(),
            "agents": {
                "planner": self.planner.backend,
                "stylist": self.stylist.backend,
                "reviewer": self.reviewer.backend,
                "semantic_retriever": self.semantic_retriever.backend,
                "composer": self.composer.backend,
                "critic": self.critic.backend,
            },
            "review_notes": list(output.review_notes),
            "diagnostics": output.diagnostics,
            "trace": list(output.trace),
            "result": presented_result,
            "structured_result": output.result.to_dict(),
        }
        if self.llm_enabled:
            payload.update(
                {
                    "llm_enabled": True,
                    "llm_call_count": output.llm_call_count,
                    "llm_attempts": output.llm_attempts,
                    "fallback_count": output.fallback_count,
                    "degraded_reason": output.degraded_reason,
                    "request_signature": output.request_signature,
                    "retrieval_plans": output.retrieval_plans,
                    "candidate_requirements": output.candidate_requirements,
                    "pool": output.pool,
                    "proposals": output.proposals,
                    "validation": output.validation,
                    "critic": output.critic,
                    "decision": output.decision,
                    "best_effort": output.best_effort,
                }
            )
        if self.llm_verbose:
            payload["llm_transcripts"] = list(self._llm_transcripts)
        return payload


def _build_parser() -> argparse.ArgumentParser:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=settings.database_path)
    parser.add_argument(
        "--embedding-dir",
        type=Path,
        default=settings.embedding_dir,
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=settings.artifact_root / "models",
    )
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--user-id", default="demo-user")
    parser.add_argument("--request", required=True)
    parser.add_argument("--max-results", type=int, default=3)
    parser.add_argument("--no-llm", action="store_true", help="Force the deterministic chain")
    parser.add_argument("--llm-verbose", action="store_true", help="Emit full LLM transcripts")
    return parser


def main() -> None:
    configure_utf8_console()
    args = _build_parser().parse_args()
    settings = Settings.from_env()
    if args.no_llm:
        # Force the deterministic chain even when a key is present in .env.
        settings = replace(settings, llm_enabled=False)
    llm_client = None
    if settings.llm_enabled:
        llm_client = llm_client_from_settings(settings)
    workflow = StyleForgeWorkflow(
        database_path=args.database,
        embedding_dir=args.embedding_dir,
        model_dir=args.model_dir,
        device=args.device,
        llm_client=llm_client,
        settings=settings,
        llm_verbose=args.llm_verbose,
    )
    payload = workflow.recommend_payload(
        user_id=args.user_id,
        request=args.request,
        max_results=args.max_results,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
