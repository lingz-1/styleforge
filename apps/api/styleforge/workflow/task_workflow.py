"""Route every task through the Multi-Agent Harness primary chain.

OUTFIT_MODIFY / OUTFIT_RECOMMEND (with an LLM) run the agentic chains; a
no-LLM OUTFIT_RECOMMEND degrades to the deterministic recommendation pipeline;
the four extension tasks run the Extension subgraph. The legacy three-agent
graph is retired — no fallback chain exists in this module.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import replace as dataclass_replace
from pathlib import Path
from time import perf_counter
from typing import Any

from styleforge.agentic.context.grounding import (
    GroundingResolver,
    unknown_latin_travel_target,
)
from styleforge.agentic.context.memory_context import PreferenceRetriever
from styleforge.agentic.context.prompt_security import scan_prompt_injection
from styleforge.agentic.environment import (
    Draft,
    Environment,
    build_facts,
    resolve_active_outfit,
)
from styleforge.agentic.harness import StyleForgeHarness
from styleforge.agentic.intent_constraints import unavailable_feature_requirements
from styleforge.agentic.tools.local_tools import (
    CAP_KNOWLEDGE,
    CAP_SKILLS,
    CAP_WEATHER,
    CAP_WEB_SEARCH,
)
from styleforge.context.builder import ContextPackBuilder
from styleforge.common.errors import ErrorCode
from styleforge.common.observability import observability, observability_context
from styleforge.core.categories import infer_slot
from styleforge.core.request_parser import parse_request
from styleforge.core.rubric import aggregate_score, dimension_keys, normalize_weights
from styleforge.core.schemas import OutfitCandidate, RecommendationResult, TaskSpec
from styleforge.knowledge.retriever import KnowledgeRetriever
from styleforge.llm.client import LlmUnavailable
from styleforge.models.agentic_contract import OutfitSnapshot
from styleforge.models.context import ContextPack
from styleforge.models.task import TaskExecutionInput
from styleforge.models.task_results import validate_task_result
from styleforge.orchestration.task_router import TaskRoute, TaskRouter, TaskType
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.run_repository import finish_run, save_candidates, start_run
from styleforge.repositories.task_run_repository import (
    fail_task_run,
    finish_task_run,
    start_task_run,
)
from styleforge.repositories.user_preferences_repository import get_environment_profile
from styleforge.repositories.wardrobe_repository import list_items
from styleforge.services.memory_aggregator import apply_evidence
from styleforge.services.memory_evidence import extract_language_evidence
from styleforge.services.presentation import present_result
from styleforge.services.recommend_result_contract import (
    normalize_outfit_recommend_result,
)
from styleforge.services.recommendation import recommend_for_user
from styleforge.tools.extension_analysis import analyze_extension_task


logger = logging.getLogger(__name__)

# H3a-2 Memory scope gate: turn-scoped requests never feed long-term memory.
# EvidenceScope only distinguishes global/contextual (no turn type), so the gate
# is a deterministic request-level check. "这次想穿黑一点" is a current-session
# preference — ThreadPreferenceView already holds it; promoting it to the user
# profile would be wrong (Turn/Thread ≠ User Profile). Conservative by design:
# 宁可漏报、不误升级.
_TURN_SCOPE_RE = re.compile(r"(这次|刚才|今天|今晚|本场|本次|眼下|现在要)")


def _scope_gate(request: str) -> bool:
    """Whether a request is turn-scoped and must skip the long-term extractor."""
    return bool(_TURN_SCOPE_RE.search(request or ""))


_FOLLOW_UP_ADJUST_WORDS = (
    "更",
    "再",
    "别",
    "不",
    "一点",
    "太",
    "有点",
    "调整",
    "改变",
    "换成",
    "换",
    "改",
    "替换",
    "色系",
    "风格",
    "正式",
    "休闲",
    "简约",
    "商务",
    "酷",
    "花",
)
_FRESH_SCENARIO_WORDS = (
    "推荐",
    "面试",
    "聚会",
    "约会",
    "通勤",
    "上班",
    "旅行",
    "婚礼",
    "出席",
    "晚宴",
    "周末",
    "今天",
    "明天",
    "穿什么",
    "搭配",
    "选一套",
)

# The four extension task types migrated into the Multi-Agent Harness (Stage
# 4c). Each runs Coordinator → Extension subgraph → closing node; no outfit
# chain, and — like the legacy ``run_extension`` they replace — no fallback:
# no LLM → ``LlmUnavailable`` (503).
_EXTENSION_TYPES = frozenset(
    {
        TaskType.STYLE_ADVICE,
        TaskType.ITEM_ADVICE,
        TaskType.WARDROBE_COMPATIBILITY,
        TaskType.WARDROBE_GAP,
    }
)


def _complete_outfit_slots(
    item_ids: list[str],
    item_type_by_id: dict[str, str],
) -> bool:
    """Require shoes plus either separates or a one-piece garment."""
    slots = {
        infer_slot(item_type_by_id.get(str(item_id), ""))
        for item_id in item_ids
    }
    return "footwear" in slots and (
        "one_piece" in slots or {"top", "bottom"} <= slots
    )


def _wardrobe_can_form_complete_outfit(
    item_type_by_id: dict[str, str],
) -> bool:
    return _complete_outfit_slots(list(item_type_by_id), item_type_by_id)


def is_follow_up(request: str) -> bool:
    """Whether a short request is a follow-up adjustment to the current outfit.

    A follow-up must be short, express an adjustment direction, and contain no
    fresh-brief scenario markers (e.g. ``更正式一点`` yes, ``明天穿什么`` no).
    """
    text = re.sub(r"\s+", " ", request.strip().lower())
    if not text or len(text) > 20:
        return False
    if not any(word in text for word in _FOLLOW_UP_ADJUST_WORDS):
        return False
    if any(word in text for word in _FRESH_SCENARIO_WORDS):
        return False
    return True


# Body regions the Agent writes in placement -> the slot word the front end
# shows (``target_slot``). Best-effort mapping only; empty when unknown.
_REGION_TO_SLOT = {
    "upper_body": "top",
    "lower_body": "bottom",
    "feet": "footwear",
    "full_body": "dress",
    "accessory": "accessory",
}


class MultiTaskWorkflow:
    """Router + the Multi-Agent Harness primary chains for all six task types."""

    def __init__(
        self,
        *,
        database_path: str,
        knowledge_root: Path,
        llm_client: Any | None,
        chroma_store: Any | None = None,
        text_embedder: Any | None = None,
        wardrobe_retriever: Any | None = None,
        # Web search for the agentic ``search_web`` tool. None degrades the
        # tool to an "unconfigured" observation; the loop keeps working.
        web_search_provider: Any | None = None,
        # Agentic recommend extras: ``get_weather`` provider + Task Skill root
        # (``knowledge/skills``). None degrades each tool to "unconfigured".
        weather_provider: Any | None = None,
        skills_root: Path | None = None,
        # H3a-3 grounding: global default city (e.g. settings.weather_default_location)
        # + injectable clock for deterministic tests.
        default_location: str = "",
        today_provider: Any | None = None,
    ) -> None:
        self.database_path = database_path
        self.knowledge_root = knowledge_root.resolve()
        self.llm_client = llm_client
        self.wardrobe_retriever = wardrobe_retriever
        self.web_search_provider = web_search_provider
        self.weather_provider = weather_provider
        self.skills_root = skills_root
        self.router = TaskRouter()
        self.context_builder = ContextPackBuilder(self.database_path)
        # Shared retriever; vector retrieval is additive and degrades to
        # keyword-only when Chroma/model loading fails.
        self.knowledge_retriever = KnowledgeRetriever(
            self.knowledge_root,
            chroma_store=chroma_store,
            text_embedder=text_embedder,
        )
        initialize_database(self.database_path)
        # H3a-3: deterministic Search-before-Ask resolver. ``today_provider`` is
        # injectable so tests freeze "today"; the resolver derives the current
        # city from device → profile → global default.
        self.grounding_resolver = GroundingResolver(
            default_location=default_location,
            today_provider=today_provider,
        )
        # H3a-4: layered long-term preference recall (read-chain tail). Shared
        # by recommend and modify; the Thread layer stays in thread_context.
        self.memory_retriever = PreferenceRetriever()

    def _route_with_session(
        self,
        task_input: TaskExecutionInput,
        session_context: dict[str, Any] | None,
    ) -> TaskRoute:
        """Route a request, folding in the session's current outfit context.

        Two passes: first route on the request alone; then, when the request is
        a short follow-up adjustment, reuse the session outfit as the current
        outfit and force a modification task.  A modification request without a
        slot word (e.g. ``更正式一点``) falls through to OUTFIT_RECOMMEND on the
        first pass and is re-routed here.
        """
        session_outfit_id = (session_context or {}).get("current_outfit_id") or ""
        session_item_ids = (session_context or {}).get("current_item_ids") or []
        has_explicit = bool(task_input.current_outfit_id or task_input.current_item_ids)
        route0 = self.router.route(
            task_input.request,
            current_outfit_id=task_input.current_outfit_id,
            has_candidate_item=task_input.candidate_item is not None,
            requested_task_type=task_input.requested_task_type,
        )
        if has_explicit or not session_item_ids:
            return route0
        with database_session(self.database_path) as connection:
            active_ids = {item.item_id for item in list_items(connection, task_input.user_id)}
        session_item_ids = [item_id for item_id in session_item_ids if item_id in active_ids]
        if not session_item_ids:
            return route0
        # The Multi-Agent Harness resolves its own targets from ``session_context``
        # (``_agentic_targets``); injecting the session outfit into ``task_input``
        # would mask "no explicit choice" and defeat "modify all three".
        if route0.task_type is TaskType.OUTFIT_RECOMMEND and is_follow_up(task_input.request):
            route = self.router.route(
                task_input.request,
                current_outfit_id=session_outfit_id,
            )
            return dataclass_replace(route, reason="session_follow_up", confidence=0.9)
        return route0

    def _extract_memories(self, task_input: TaskExecutionInput) -> None:
        """Distill preference evidence via LLM and aggregate it; failures swallowed."""
        # H3a-2 scope gate: a turn-scoped request stays in ThreadPreferenceView
        # and never promotes to the user profile (covers the legacy + agentic
        # call sites 748/915/1246 in one change).
        if _scope_gate(task_input.request):
            return
        try:
            evidence = extract_language_evidence(self.llm_client, task_input.request)
            if not evidence:
                return
            with database_session(self.database_path) as connection:
                apply_evidence(connection, task_input.user_id, evidence)
        except Exception as error:
            # Memory extraction is best-effort and must never fail a task run.
            logger.warning(
                "memory_extraction_dropped user_id=%s error_type=%s",
                task_input.user_id,
                type(error).__name__,
            )

    def _record_task_failure(
        self,
        *,
        run_id: str,
        error: Exception,
        context_pack: dict[str, Any],
    ) -> None:
        """Persist a failed run without ever masking the original exception."""
        try:
            with database_session(self.database_path) as connection:
                fail_task_run(
                    connection,
                    run_id=run_id,
                    error=error,
                    context_pack=context_pack,
                )
        except Exception as journal_error:
            logger.exception(
                "task_failure_journal_failed run_id=%s root_error_type=%s journal_error_type=%s",
                run_id,
                type(error).__name__,
                type(journal_error).__name__,
            )

    def execute(
        self,
        task_input: TaskExecutionInput,
        *,
        session_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        started_at = perf_counter()
        run_id = ""
        try:
            security_inputs = [("request", task_input.request)]
            if task_input.candidate_item is not None:
                security_inputs.extend(
                    (
                        ("candidate_name", task_input.candidate_item.name),
                        ("candidate_description", task_input.candidate_item.description),
                    )
                )
            security_categories = {
                category
                for source, value in security_inputs
                for category in scan_prompt_injection(
                    value, source=f"task_input:{source}"
                ).categories
            }
            if security_categories:
                observability.record_operation(
                    "prompt_security",
                    success=True,
                    degraded=True,
                    component="prompt_security",
                    source="task_input",
                    signal_count=len(security_categories),
                    categories=sorted(security_categories),
                )
            route = self._route_with_session(task_input, session_context)
            with database_session(self.database_path) as connection:
                run_id = start_task_run(
                    connection,
                    user_id=task_input.user_id,
                    task_type=route.task_type,
                    request=task_input.request,
                )
            with observability_context(run_id=run_id):
                result = self._execute_started_run(
                    task_input,
                    route,
                    run_id,
                    session_context,
                )
        except Exception as error:
            code = (
                ErrorCode.LLM_UNAVAILABLE.value
                if isinstance(error, LlmUnavailable)
                else ErrorCode.AGENT_EXECUTION_FAILED.value
            )
            observability.record_operation(
                "task_run",
                success=False,
                duration_ms=(perf_counter() - started_at) * 1000.0,
                retryable=isinstance(error, (LlmUnavailable, TimeoutError, ConnectionError)),
                error_code=code,
                component="task_workflow",
                run_id=run_id,
                task_type=(route.task_type.value if "route" in locals() else "unknown"),
                error_type=type(error).__name__,
            )
            raise
        observability.record_operation(
            "task_run",
            success=True,
            duration_ms=(perf_counter() - started_at) * 1000.0,
            component="task_workflow",
            run_id=run_id,
            task_type=route.task_type.value,
            status=str(result.get("status") or ""),
        )
        return result

    def _execute_started_run(
        self,
        task_input: TaskExecutionInput,
        route: TaskRoute,
        run_id: str,
        session_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        try:
            initial_context = self.context_builder.build(task_input, route)
        except Exception as error:
            self._record_task_failure(run_id=run_id, error=error, context_pack={})
            raise
        # Every task runs the Multi-Agent Harness as the primary chain. A
        # no-LLM OUTFIT_RECOMMEND degrades to the deterministic recommendation
        # pipeline; no-LLM modify / extension raise ``LlmUnavailable`` (503)
        # inside their run wrappers, matching the retired legacy chains.
        if route.task_type is TaskType.OUTFIT_MODIFY:
            return self._run_agentic_modify(
                task_input, route, initial_context, run_id, session_context
            )
        if route.task_type is TaskType.OUTFIT_RECOMMEND:
            if self.llm_client is not None:
                return self._run_agentic_recommend(
                    task_input, route, initial_context, run_id, session_context
                )
            return self._deterministic_recommend(task_input, route, initial_context, run_id)
        if route.task_type in _EXTENSION_TYPES:
            return self._run_agentic_extension(
                task_input, route, initial_context, run_id, session_context
            )
        error = ValueError(f"Unhandled task type: {route.task_type}")
        self._record_task_failure(run_id=run_id, error=error, context_pack={})
        raise error

    # --- no-LLM OUTFIT_RECOMMEND: deterministic pipeline ------------------

    def _deterministic_recommend(
        self,
        task_input: TaskExecutionInput,
        route: TaskRoute,
        initial_context: ContextPack,
        run_id: str,
    ) -> dict[str, Any]:
        """Run OUTFIT_RECOMMEND without an LLM through the deterministic pipeline.

        ``parse_request`` → ``recommend_for_user`` → ``present_result`` — the
        same no-key recommend contract as before the legacy graph was retired.
        No agent call is made; the payload marks ``llm_enabled=false``.
        """
        context_json = initial_context.model_dump(mode="json")
        try:
            parsed = parse_request(
                task_input.user_id,
                task_input.request,
                task_input.max_results,
            )
            recommendation = recommend_for_user(self.database_path, parsed)
            result = present_result(self.database_path, recommendation)
            status = str(recommendation.status)
            result = normalize_outfit_recommend_result(
                result,
                run_id=run_id,
                status=status,
            )
            with database_session(self.database_path) as connection:
                finish_task_run(
                    connection,
                    run_id=run_id,
                    status=status,
                    context_pack=context_json,
                    result=result,
                )
            self._extract_memories(task_input)
        except Exception as error:
            self._record_task_failure(
                run_id=run_id,
                error=error,
                context_pack=context_json,
            )
            raise
        return {
            "run_id": run_id,
            "user_id": task_input.user_id,
            "request": task_input.request,
            "task_type": TaskType.OUTFIT_RECOMMEND.value,
            "selected_subgraph": route.subgraph,
            "route": route.to_dict(),
            "status": status,
            "context_pack": context_json,
            "result": result,
            "trace": [],
            "diagnostics": {},
            "image_endpoint_template": "/items/{item_id}/image",
            "llm_enabled": False,
            "llm_call_count": 0,
        }

    # --- Stage 4: agentic primary chain ----------------------------------

    def _harness(
        self,
        environment: Environment,
        *,
        target_candidates: int = 3,
    ) -> StyleForgeHarness:
        """Assemble the Multi-Agent Harness over one environment.

        Runtime capabilities are derived from the providers actually deployed on
        this workflow (Layer 2), so ``tools=`` changes only with real capability
        availability — never with the request.
        """
        return StyleForgeHarness(
            llm=self.llm_client,
            environment=environment,
            runtime_capabilities=self._runtime_capabilities(),
            knowledge_retriever=self.knowledge_retriever,
            target_candidates=target_candidates,
            # H3a-4: layered Top-K recall over ``raw_preferences`` (recommend +
            # modify share one retriever; the Thread layer stays in
            # thread_context, two chains never promote into each other).
            memory_retriever=self.memory_retriever,
        )

    def _runtime_capabilities(self) -> frozenset[str]:
        """Deployed capability keys (Layer 2) from the workflow's providers."""
        caps: set[str] = set()
        if self.web_search_provider is not None and bool(
            getattr(self.web_search_provider, "available", True)
        ):
            caps.add(CAP_WEB_SEARCH)
        if self.weather_provider is not None:
            caps.add(CAP_WEATHER)
        if self.knowledge_retriever is not None:
            caps.add(CAP_KNOWLEDGE)
        if self.skills_root is not None:
            caps.add(CAP_SKILLS)
        return frozenset(caps)

    @staticmethod
    def _task_diagnostics(
        outcomes: dict[str, Any] | list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Aggregate safe wardrobe-search telemetry for response and storage."""
        outcome_list = outcomes if isinstance(outcomes, list) else [outcomes]
        allowed_keys = (
            "mode",
            "matched",
            "returned",
            "semantic_available",
            "degraded",
            "duration_ms",
            "requested_slots",
            "errors",
            "query_chars",
        )
        calls = [
            {key: call.get(key) for key in allowed_keys if key in call}
            for outcome in outcome_list
            for call in outcome.get("wardrobe_retrievals") or []
            if isinstance(call, dict)
        ]
        modes = list(dict.fromkeys(str(call.get("mode") or "keyword") for call in calls))
        errors = sorted(
            {str(error) for call in calls for error in call.get("errors") or [] if error}
        )
        mcp_allowed_keys = (
            "call_id",
            "server",
            "tool",
            "transport",
            "success",
            "duration_ms",
            "error_code",
            "result_chars",
            "called_at",
        )
        mcp_calls = [
            {key: call.get(key) for key in mcp_allowed_keys if key in call}
            for outcome in outcome_list
            for call in outcome.get("mcp_calls") or []
            if isinstance(call, dict)
        ]
        return {
            "wardrobe_retrieval": {
                "calls": len(calls),
                "modes": modes,
                "semantic_used": any(call.get("mode") in {"semantic", "hybrid"} for call in calls),
                "keyword_fallback_calls": sum(call.get("mode") == "keyword" for call in calls),
                "degraded_calls": sum(bool(call.get("degraded")) for call in calls),
                "total_duration_ms": round(
                    sum(float(call.get("duration_ms") or 0.0) for call in calls),
                    2,
                ),
                "errors": errors,
                "details": calls,
            },
            "mcp": {
                "calls": len(mcp_calls),
                "successful_calls": sum(bool(call.get("success")) for call in mcp_calls),
                "failed_calls": sum(not bool(call.get("success")) for call in mcp_calls),
                "servers": list(
                    dict.fromkeys(
                        str(call.get("server")) for call in mcp_calls if call.get("server")
                    )
                ),
                "tools": list(
                    dict.fromkeys(str(call.get("tool")) for call in mcp_calls if call.get("tool"))
                ),
                "total_duration_ms": round(
                    sum(float(call.get("duration_ms") or 0.0) for call in mcp_calls),
                    2,
                ),
                "errors": sorted(
                    {str(call.get("error_code")) for call in mcp_calls if call.get("error_code")}
                ),
                "details": mcp_calls,
            },
        }

    def _thread_context(
        self,
        session_context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Session-scoped context for the harness (H3a-2).

        Carries the current outfit anchor plus the thread-scoped preference /
        grounding views that api.py updates right after the user message is
        accepted. A missing session yields ``None`` (no thread layer for
        single-shot callers).
        """
        if not session_context:
            return None
        return {
            "current_outfit_id": session_context.get("current_outfit_id") or "",
            "current_item_ids": list(session_context.get("current_item_ids") or []),
            "session_signals": session_context.get("session_signals") or {},
            "thread_preferences": session_context.get("thread_preferences") or {},
            "thread_grounding": session_context.get("thread_grounding") or {},
        }

    def _environment_profile(self, connection, user_id: str) -> dict[str, Any]:
        """User's default city/timezone for grounding (device → profile → global).

        Best-effort: a missing or malformed profile yields ``{}`` so grounding
        falls through to the global default instead of failing the run.
        """
        try:
            return get_environment_profile(connection, user_id)
        except Exception as error:
            logger.warning(
                "environment_profile_unavailable user_id=%s error_type=%s",
                user_id,
                type(error).__name__,
            )
            return {}

    def _raw_preferences(self, initial_context: ContextPack) -> list[Any]:
        """Normalized memory profile for the PreferenceRetriever (H3a-3).

        ``memory_profile`` may be a flat list of preference entries or a dict
        carrying a ``preferences`` key; the retriever consumes a flat list.
        """
        profile = (initial_context.user_context.preferences or {}).get("memory_profile") or []
        if isinstance(profile, dict):
            return list(profile.get("preferences") or [])
        return list(profile)

    def _preference_context(self, outcome: dict[str, Any]) -> list[dict[str, Any]]:
        """Layered Top-K preference view for the result payload (H3a-4).

        Same retriever the harness prompt actually fed the Stylist, re-run once
        over the final outcome state — so the front end renders the *new* layered
        memory context (短期/场景/长期/避免, ≤ 8 rows) instead of dumping the
        full legacy ``memory_profile`` (~12 K chars of raw preferences).
        """
        return self.memory_retriever.retrieve("stylist", outcome)

    def _grounding_context(
        self,
        task_input: TaskExecutionInput,
        initial_context: ContextPack,
        connection,
        thread_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Deterministic grounding facts for one invoke (H3a-3)."""
        grounding = self.grounding_resolver.resolve(
            task_input.request,
            location_context=task_input.location_context,
            environment_profile=self._environment_profile(connection, task_input.user_id),
            thread_context=thread_context,
            capabilities=self._runtime_capabilities(),
        )
        return grounding.model_dump(mode="json")

    def _run_agentic_modify(
        self,
        task_input: TaskExecutionInput,
        route: TaskRoute,
        initial_context: ContextPack,
        run_id: str,
        session_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run OUTFIT_MODIFY through the Multi-Agent Harness as the primary chain.

        Each target outfit (an explicit ``current_outfit_id``, or every recent
        recommendation candidate — "modify all three") becomes one
        ``StyleForgeHarness.invoke`` over that snapshot as ``base_draft``; the
        batch of outcomes is wrapped into one result with one alternative per
        success. Completed alternatives are persisted to candidate_outfits so a
        later turn can re-anchor on the new outfit_id.
        """
        context_json = initial_context.model_dump(mode="json")
        try:
            if self.llm_client is None:
                raise LlmUnavailable("OUTFIT_MODIFY requires an LLM client")
            targets = self._agentic_targets(task_input, session_context)
            outcomes: list[dict[str, Any]] = []
            item_type_by_id: dict[str, str] = {}
            with database_session(self.database_path) as connection:
                wardrobe_items = list_items(connection, task_input.user_id)
                item_type_by_id = {item.item_id: item.item_type for item in wardrobe_items}
                unavailable = unavailable_feature_requirements(
                    task_input.request,
                    wardrobe_items,
                )
                if not _wardrobe_can_form_complete_outfit(item_type_by_id) or unavailable:
                    outcomes = [
                        {
                            "status": "infeasible",
                            "candidates": [],
                            "wardrobe_retrievals": [],
                            "mcp_calls": [],
                            "_llm_call_count": 0,
                            "infeasible_reason": (
                                "intent_requirements_unavailable"
                                if unavailable
                                else "wardrobe_missing_complete_outfit_slots"
                            ),
                            "intent_constraint_issues": [
                                requirement.reason for requirement in unavailable
                            ],
                        }
                        for _target in targets
                    ]
                else:
                    for target in targets:
                        target_item_ids = list(target.get("item_ids") or [])
                        if not target_item_ids and target.get("outfit_id"):
                            lookup_input = task_input.model_copy(
                                update={
                                    "current_outfit_id": target["outfit_id"],
                                    "current_item_ids": [],
                                }
                            )
                            stored = resolve_active_outfit(connection, lookup_input, None)
                            if stored is not None:
                                target_item_ids = list(stored.item_ids)
                        target["item_ids"] = target_item_ids
                        if not target_item_ids:
                            outcomes.append(
                                {
                                    "status": "needs_clarification",
                                    "clarification_question": "请先选择要修改的当前搭配。",
                                    "candidates": [],
                                    "wardrobe_retrievals": [],
                                    "mcp_calls": [],
                                    "_llm_call_count": 0,
                                }
                            )
                            continue
                        sub_input = task_input.model_copy(
                            update={
                                "current_outfit_id": target["outfit_id"],
                                "current_item_ids": target_item_ids,
                            }
                        )
                        facts = build_facts(
                            connection, sub_input, initial_context, wardrobe_items
                        )
                        environment = Environment(
                            connection,
                            wardrobe_items,
                            facts,
                            search_limit=12,
                            wardrobe_retriever=self.wardrobe_retriever,
                            web_search_provider=self.web_search_provider,
                        )
                        harness = self._harness(environment, target_candidates=1)
                        base_snapshot = environment.snapshot_outfit(
                            target["outfit_id"],
                            target_item_ids,
                        )
                        thread_context = self._thread_context(session_context)
                        outcome = harness.invoke(
                            {
                                "run_id": run_id,
                                "request": task_input.request,
                                "task_type": TaskType.OUTFIT_MODIFY.value,
                                "base_draft": Draft(outfit=base_snapshot, layers={}),
                                "thread_context": thread_context,
                                "grounding_context": self._grounding_context(
                                    task_input,
                                    initial_context,
                                    connection,
                                    thread_context,
                                ),
                                "raw_preferences": self._raw_preferences(initial_context),
                                "grounding_attempted_kinds": [],
                                "grounding_resolved_kinds": [],
                            }
                        )
                        outcome["wardrobe_retrievals"] = list(
                            environment.wardrobe_search_diagnostics
                        )
                        outcome["mcp_calls"] = list(environment.mcp_call_traces)
                        outcome["_llm_call_count"] = harness.model_calls
                        outcomes.append(outcome)
            diagnostics = self._task_diagnostics(outcomes)
            result = self._agentic_modify_to_result(
                task_input,
                targets,
                outcomes,
                item_type_by_id,
                evaluation_weights=self._evaluation_weights(initial_context),
            )
            status = str(result.get("status", "infeasible"))
            with database_session(self.database_path) as connection:
                finish_task_run(
                    connection,
                    run_id=run_id,
                    status=status,
                    context_pack=context_json,
                    result=result,
                    diagnostics=diagnostics,
                )
            if status == "completed":
                self._persist_agentic_candidate(task_input, result)
            self._extract_memories(task_input)
        except Exception as error:
            self._record_task_failure(
                run_id=run_id,
                error=error,
                context_pack=context_json,
            )
            raise
        return {
            "run_id": run_id,
            "user_id": task_input.user_id,
            "request": task_input.request,
            "task_type": TaskType.OUTFIT_MODIFY.value,
            "selected_subgraph": "agentic_harness",
            "route": route.to_dict(),
            "status": status,
            "context_pack": context_json,
            "result": result,
            "trace": [],
            "diagnostics": diagnostics,
            "image_endpoint_template": "/items/{item_id}/image",
            "agents": {"harness": "styleforge_harness"},
            "agentic_outcome": outcomes[0] if len(outcomes) == 1 else outcomes,
            "llm_enabled": True,
            "llm_call_count": sum(int(outcome.get("_llm_call_count", 0)) for outcome in outcomes),
        }

    def _agentic_modify_to_result(
        self,
        task_input: TaskExecutionInput,
        targets: list[dict[str, Any]],
        outcomes: list[dict[str, Any]],
        item_type_by_id: dict[str, str] | None = None,
        evaluation_weights: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Wrap one or more Harness modify outcomes into the OutfitModifyResult
        contract — one alternative per successful target."""
        if len(outcomes) == 1:
            return self._agentic_modify_outcome_to_result(
                task_input,
                targets[0],
                outcomes[0],
                item_type_by_id,
                evaluation_weights=evaluation_weights,
            )
        item_type_by_id = item_type_by_id or {}
        base_id = task_input.current_outfit_id or ""
        alternatives: list[dict[str, Any]] = []
        skipped = 0
        first_failure: dict[str, Any] | None = None
        first_success: dict[str, Any] | None = None
        for target, outcome in zip(targets, outcomes):
            alternative, reason = self._harness_modify_alternative(
                task_input,
                target,
                outcome,
                item_type_by_id,
                evaluation_weights=evaluation_weights,
            )
            if alternative is not None:
                alternatives.append(alternative)
                if first_success is None:
                    first_success = alternative
            else:
                if first_failure is None:
                    first_failure = outcome
                if reason == "skipped":
                    skipped += 1
        if not alternatives:
            if (
                first_failure is not None
                and str(first_failure.get("status")) == "needs_clarification"
            ):
                question = str(first_failure.get("clarification_question") or "需要你进一步说明")
                return validate_task_result(
                    TaskType.OUTFIT_MODIFY,
                    {
                        "status": "needs_clarification",
                        "current_outfit_id": base_id,
                        "target_slot": "",
                        "replaced_item_ids": [],
                        "locked_item_ids": [],
                        "alternatives": [],
                        "message": question,
                        "clarification_question": question,
                    },
                )
            return validate_task_result(
                TaskType.OUTFIT_MODIFY,
                {
                    "status": "infeasible",
                    "current_outfit_id": base_id,
                    "target_slot": "",
                    "replaced_item_ids": [],
                    "locked_item_ids": [],
                    "alternatives": [],
                    "message": "修改失败，请换个说法重试",
                },
            )
        all_replaced = [
            item_id
            for alternative in alternatives
            for item_id in alternative.get("replaced_item_ids", [])
        ]
        all_locked = [
            item_id
            for alternative in alternatives
            for item_id in alternative.get("locked_item_ids", [])
        ]
        all_added = [
            item_id
            for alternative in alternatives
            for item_id in alternative.get("added_item_ids", [])
        ]
        assert first_success is not None
        target_slot = ""
        changed_ids = [
            *first_success.get("replaced_item_ids", []),
            *first_success.get("added_item_ids", []),
        ]
        for item_id in changed_ids:
            item_type = item_type_by_id.get(item_id)
            if item_type:
                slot = infer_slot(item_type)
                if slot and slot != "other":
                    target_slot = slot
                    break
        detail = (
            f"已按你的要求修改 {len(alternatives)} 套"
            if not skipped
            else f"已修改 {len(alternatives)} 套，另有 {skipped} 套候选单品过少已跳过"
        )
        return validate_task_result(
            TaskType.OUTFIT_MODIFY,
            {
                "status": "completed",
                "current_outfit_id": alternatives[0]["outfit_id"],
                "target_slot": target_slot,
                "replaced_item_ids": all_replaced,
                "added_item_ids": all_added,
                "locked_item_ids": all_locked,
                "alternatives": alternatives,
                "message": detail,
            },
        )

    def _agentic_modify_outcome_to_result(
        self,
        task_input: TaskExecutionInput,
        target: dict[str, Any],
        outcome: dict[str, Any],
        item_type_by_id: dict[str, str] | None = None,
        evaluation_weights: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Wrap ONE Harness modify outcome into the OutfitModifyResult contract.

        Status mapping: done-with-candidate -> completed, needs_clarification ->
        needs_clarification, otherwise infeasible. An under-sized candidate
        surfaces as a clarification (the contract requires >= 2 items).
        """
        item_type_by_id = item_type_by_id or {}
        base_id = target.get("outfit_id") or task_input.current_outfit_id or ""
        if str(outcome.get("status")) == "needs_clarification":
            question = str(outcome.get("clarification_question") or "需要你进一步说明")
            return validate_task_result(
                TaskType.OUTFIT_MODIFY,
                {
                    "status": "needs_clarification",
                    "current_outfit_id": base_id,
                    "target_slot": "",
                    "replaced_item_ids": [],
                    "locked_item_ids": [],
                    "alternatives": [],
                    "message": question,
                    "clarification_question": question,
                },
            )
        alternatives: list[dict[str, Any]] = []
        reason = "failed"
        for candidate_index, _candidate in enumerate(outcome.get("candidates") or []):
            alternative, candidate_reason = self._harness_modify_alternative(
                task_input,
                target,
                outcome,
                item_type_by_id,
                evaluation_weights=evaluation_weights,
                candidate_index=candidate_index,
            )
            if alternative is not None:
                alternatives.append(alternative)
            else:
                reason = candidate_reason
        if not alternatives:
            if reason == "skipped":
                message = "当前搭配单品过少，请补充想怎么调整"
                return validate_task_result(
                    TaskType.OUTFIT_MODIFY,
                    {
                        "status": "needs_clarification",
                        "current_outfit_id": base_id,
                        "target_slot": "",
                        "replaced_item_ids": [],
                        "locked_item_ids": [],
                        "alternatives": [],
                        "message": message,
                        "clarification_question": message,
                    },
                )
            return validate_task_result(
                TaskType.OUTFIT_MODIFY,
                {
                    "status": "infeasible",
                    "current_outfit_id": base_id,
                    "target_slot": "",
                    "replaced_item_ids": [],
                    "locked_item_ids": [],
                    "alternatives": [],
                    "message": "修改失败，请换个说法重试",
                },
            )
        target_slot = ""
        changed_ids = [
            *alternatives[0].get("replaced_item_ids", []),
            *alternatives[0].get("added_item_ids", []),
        ]
        for item_id in changed_ids:
            item_type = item_type_by_id.get(item_id)
            if item_type:
                slot = infer_slot(item_type)
                if slot and slot != "other":
                    target_slot = slot
                    break
        all_replaced = list(
            dict.fromkeys(
                item_id
                for alternative in alternatives
                for item_id in alternative.get("replaced_item_ids", [])
            )
        )
        all_locked = list(
            dict.fromkeys(
                item_id
                for alternative in alternatives
                for item_id in alternative.get("locked_item_ids", [])
            )
        )
        all_added = list(
            dict.fromkeys(
                item_id
                for alternative in alternatives
                for item_id in alternative.get("added_item_ids", [])
            )
        )
        return validate_task_result(
            TaskType.OUTFIT_MODIFY,
            {
                "status": "completed",
                "current_outfit_id": alternatives[0]["outfit_id"],
                "target_slot": target_slot,
                "replaced_item_ids": all_replaced,
                "added_item_ids": all_added,
                "locked_item_ids": all_locked,
                "alternatives": alternatives,
                "message": f"已按你的要求生成 {len(alternatives)} 个修改方案",
            },
        )

    def _harness_modify_alternative(
        self,
        task_input: TaskExecutionInput,
        target: dict[str, Any],
        outcome: dict[str, Any],
        item_type_by_id: dict[str, str],
        evaluation_weights: dict[str, Any] | None = None,
        candidate_index: int = 0,
    ) -> tuple[dict[str, Any] | None, str]:
        """One alternative from one Harness modify outcome (``candidates[0]``).

        ``replaced`` is the base target set minus the final set (the Harness
        keeps no per-op trail in the state); ``locked`` is their intersection.
        Returns ``(None, reason)`` when the outcome produced no usable candidate.
        """
        candidates = list(outcome.get("candidates") or [])
        if not candidates:
            return None, "failed"
        if candidate_index < 0 or candidate_index >= len(candidates):
            return None, "failed"
        candidate = candidates[candidate_index]
        item_ids = list(candidate.get("item_ids") or [])
        if len(item_ids) < 2:
            return None, "skipped"
        if not _complete_outfit_slots(item_ids, item_type_by_id):
            return None, "incomplete"
        base_item_ids = list(target.get("item_ids") or [])
        if set(item_ids) == set(base_item_ids):
            return None, "unchanged"
        base_id = target.get("outfit_id") or task_input.current_outfit_id or ""
        new_outfit_id = f"{base_id or 'outfit'}-mod-{uuid.uuid4().hex[:6]}"
        replaced = [item_id for item_id in base_item_ids if item_id not in item_ids]
        locked = [item_id for item_id in base_item_ids if item_id in item_ids]
        added = [item_id for item_id in item_ids if item_id not in base_item_ids]
        changed_slots = list(
            dict.fromkeys(
                infer_slot(item_type_by_id.get(item_id, ""))
                for item_id in [*replaced, *added]
                if infer_slot(item_type_by_id.get(item_id, "")) not in {"", "other"}
            )
        )
        slot_labels = {
            "top": "上衣",
            "bottom": "下装",
            "one_piece": "连衣裙",
            "footwear": "鞋履",
            "outerwear": "外套",
            "bag": "包",
            "accessory": "配饰",
        }
        changed_text = "、".join(slot_labels.get(slot, slot) for slot in changed_slots)
        if replaced and added:
            reasoning = f"已替换{changed_text or '目标单品'}，其余原搭配单品保持不变。"
        elif added:
            reasoning = (
                f"当前搭配原本没有{changed_text or '目标类别'}，"
                "已从现有衣橱补入该单品，其余原搭配单品保持不变。"
            )
        else:
            reasoning = f"已移除{changed_text or '目标单品'}，其余原搭配单品保持不变。"
        score = self._score_harness_candidate(candidate, evaluation_weights)
        acceptance_status = str(candidate.get("status") or "STAGED")
        degraded_reason = self._candidate_degraded_reason(candidate)
        if acceptance_status == "DEGRADED_ACCEPTED":
            # The Critic's free-form feedback may ask to confirm facts already
            # present in the authoritative base snapshot, or tell the user to
            # search after the system has completed the change. Keep the honest
            # degraded status while exposing only a factually stable warning.
            degraded_reason = (
                "语义审校未完全通过；该备选已通过衣橱归属、结构和前后差量校验。"
            )
        return (
            {
                "outfit_id": new_outfit_id,
                "item_ids": item_ids,
                "reasoning": reasoning,
                "replaced_item_ids": replaced,
                "added_item_ids": added,
                "locked_item_ids": locked,
                "hard_valid": bool(candidate.get("environment_valid", False)),
                "score": score["score"],
                "llm_score": score["llm_score"],
                "score_details": score["score_details"],
                "dimension_scores": score["dimension_scores"],
                "evaluation_weights": score["evaluation_weights"],
                "score_source": score["score_source"],
                "acceptance_status": acceptance_status,
                "degraded_reason": degraded_reason,
            },
            "ok",
        )

    # --- Stage 4b: agentic primary recommend chain -----------------------

    def _run_agentic_recommend(
        self,
        task_input: TaskExecutionInput,
        route: TaskRoute,
        initial_context: ContextPack,
        run_id: str,
        session_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run OUTFIT_RECOMMEND through the Multi-Agent Harness as the primary chain.

        One ``StyleForgeHarness.invoke`` produces all three candidates from the
        empty base draft: Coordinator → Research (skill / web / weather run
        ONCE) → Evidence Synthesizer → Stylist ×3 sharing the evidence → Main
        Graph gates → StageCandidate. The single outcome rides
        ``agentic_outcome`` so the front end can render the research basis; the
        ``get_weather`` fact the Research Agent actually saw (if any) rides
        ``environment_context``.

        Requires an LLM; execute gates on ``llm_client`` so a no-key request
        degrades to the legacy graph instead of raising.
        """
        if self.llm_client is None:
            raise LlmUnavailable("OUTFIT_RECOMMEND requires an LLM client")
        context_json = initial_context.model_dump(mode="json")
        harness: StyleForgeHarness | None = None
        try:
            thread_context = self._thread_context(session_context)
            with database_session(self.database_path) as connection:
                wardrobe_items = list_items(connection, task_input.user_id)
                item_type_by_id = {item.item_id: item.item_type for item in wardrobe_items}
                unavailable = unavailable_feature_requirements(
                    task_input.request,
                    wardrobe_items,
                )
                facts = build_facts(connection, task_input, initial_context, wardrobe_items)
                grounding_context = self._grounding_context(
                    task_input, initial_context, connection, thread_context
                )
            model_calls = 0
            ambiguous_target = unknown_latin_travel_target(task_input.request)
            if ambiguous_target:
                question = (
                    f"“{ambiguous_target}”具体指哪个城市、地点或活动？"
                    "请补充名称或纠正拼写，我再根据场景给你搭配。"
                )
                outcome = {
                    "status": "needs_clarification",
                    "clarification_question": question,
                    "candidates": [],
                    "wardrobe_retrievals": [],
                    "mcp_calls": [],
                    "infeasible_reason": "ambiguous_travel_target",
                }
                weather_facts = None
            elif not _wardrobe_can_form_complete_outfit(item_type_by_id) or unavailable:
                outcome = {
                    "status": "infeasible",
                    "candidates": [],
                    "wardrobe_retrievals": [],
                    "mcp_calls": [],
                    "infeasible_reason": (
                        "intent_requirements_unavailable"
                        if unavailable
                        else "wardrobe_missing_complete_outfit_slots"
                    ),
                    "intent_constraint_issues": [
                        requirement.reason for requirement in unavailable
                    ],
                }
                weather_facts = None
            else:
                # No transaction is held while waiting on LLM/MCP calls. Personal
                # vector lookup opens a short-lived session only when the tool runs.
                environment = Environment(
                    None,
                    wardrobe_items,
                    facts,
                    search_limit=12,
                    wardrobe_retriever=self.wardrobe_retriever,
                    web_search_provider=self.web_search_provider,
                    weather_provider=self.weather_provider,
                    skills_root=self.skills_root,
                    connection_factory=lambda: database_session(self.database_path),
                )
                harness = self._harness(
                    environment, target_candidates=task_input.max_results
                )
                outcome = harness.invoke(
                    {
                        "run_id": run_id,
                        "request": task_input.request,
                        "task_type": TaskType.OUTFIT_RECOMMEND.value,
                        "base_draft": Draft(
                            outfit=OutfitSnapshot(
                                outfit_id="base", item_ids=[], items=[]
                            ),
                            layers={},
                        ),
                        "thread_context": thread_context,
                        "grounding_context": grounding_context,
                        "raw_preferences": self._raw_preferences(initial_context),
                        "grounding_attempted_kinds": [],
                        "grounding_resolved_kinds": [],
                    }
                )
                outcome["wardrobe_retrievals"] = list(
                    environment.wardrobe_search_diagnostics
                )
                outcome["mcp_calls"] = list(environment.mcp_call_traces)
                weather_facts = environment.last_weather_facts
                model_calls = harness.model_calls
            # H3a: expose the layered preference view the Stylist actually
            # saw so the front end renders the new agentic context (环境定位
            # + 分层偏好 + 对话上下文) instead of the full legacy dump.
            outcome["preference_context"] = self._preference_context(outcome)
            diagnostics = self._task_diagnostics(outcome)
            result = self._agentic_recommend_to_result(
                task_input,
                outcome,
                item_type_by_id,
                weather_facts=weather_facts,
                llm_call_count=model_calls,
                evaluation_weights=self._evaluation_weights(initial_context),
            )
            status = str(result.get("status", "infeasible"))
            result = normalize_outfit_recommend_result(
                result,
                run_id=run_id,
                status=status,
            )
            with database_session(self.database_path) as connection:
                finish_task_run(
                    connection,
                    run_id=run_id,
                    status=status,
                    context_pack=context_json,
                    result=result,
                    diagnostics=diagnostics,
                )
            if status == "completed":
                self._persist_agentic_recommend(task_input, result)
            if status == "completed":
                self._extract_memories(task_input)
        except Exception as error:
            self._record_task_failure(
                run_id=run_id,
                error=error,
                context_pack=context_json,
            )
            raise
        return {
            "run_id": run_id,
            "user_id": task_input.user_id,
            "request": task_input.request,
            "task_type": TaskType.OUTFIT_RECOMMEND.value,
            "selected_subgraph": "agentic_harness",
            "route": route.to_dict(),
            "status": status,
            "context_pack": context_json,
            "result": result,
            "trace": [],
            "diagnostics": diagnostics,
            "image_endpoint_template": "/items/{item_id}/image",
            "agents": {"harness": "styleforge_harness"},
            "agentic_outcome": outcome,
            "llm_enabled": True,
            "llm_call_count": model_calls,
        }

    # --- Stage 4c: agentic primary extension chain ------------------------

    def _run_agentic_extension(
        self,
        task_input: TaskExecutionInput,
        route: TaskRoute,
        initial_context: ContextPack,
        run_id: str,
        session_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run the four extension task types through the Multi-Agent Harness.

        The deterministic facts (``analyze_extension_task``) are pre-computed
        here — that analysis carries Runtime Dependencies (database_path /
        knowledge_root) that must never enter the Execution State, so it is not
        a tool; it is handed to the Extension subgraph as ``extension_facts``.
        The Extension agent enriches the facts via the search tools, the closing
        node hard-validates the task contract, and ``extension_result`` rides
        ``agentic_outcome`` so the front end keeps rendering the exact legacy
        ``payload.result`` shape.

        Requires an LLM: no model → ``LlmUnavailable`` (503), matching the
        legacy ``run_extension`` which had no fallback either.
        """
        context_json = initial_context.model_dump(mode="json")
        harness: StyleForgeHarness | None = None
        try:
            if self.llm_client is None:
                raise LlmUnavailable("OUTFIT extension tasks require an LLM client")
            with database_session(self.database_path) as connection:
                wardrobe_items = list_items(connection, task_input.user_id)
                facts = build_facts(connection, task_input, initial_context, wardrobe_items)
                environment = Environment(
                    connection,
                    wardrobe_items,
                    facts,
                    search_limit=12,
                    wardrobe_retriever=self.wardrobe_retriever,
                    web_search_provider=self.web_search_provider,
                    weather_provider=self.weather_provider,
                    skills_root=self.skills_root,
                )
                extension_facts = analyze_extension_task(
                    database_path=self.database_path,
                    knowledge_root=self.knowledge_root,
                    task_input=task_input,
                    route=route,
                    context_pack=initial_context,
                    knowledge_retriever=self.knowledge_retriever,
                ).model_dump(mode="json")
                harness = self._harness(environment)
                thread_context = self._thread_context(session_context)
                outcome = harness.invoke(
                    {
                        "run_id": run_id,
                        "request": task_input.request,
                        "task_type": route.task_type.value,
                        "base_draft": Draft(
                            outfit=OutfitSnapshot(outfit_id="base", item_ids=[], items=[]),
                            layers={},
                        ),
                        "thread_context": thread_context,
                        "grounding_context": self._grounding_context(
                            task_input, initial_context, connection, thread_context
                        ),
                        "raw_preferences": self._raw_preferences(initial_context),
                        "grounding_attempted_kinds": [],
                        "grounding_resolved_kinds": [],
                        "extension_facts": extension_facts,
                    }
                )
                outcome["wardrobe_retrievals"] = list(environment.wardrobe_search_diagnostics)
                outcome["mcp_calls"] = list(environment.mcp_call_traces)
            assert harness is not None
            diagnostics = self._task_diagnostics(outcome)
            result = self._agentic_extension_to_result(task_input, outcome)
            status = str(result.get("status", "infeasible"))
            with database_session(self.database_path) as connection:
                finish_task_run(
                    connection,
                    run_id=run_id,
                    status=status,
                    context_pack=context_json,
                    result=result,
                    diagnostics=diagnostics,
                )
            self._extract_memories(task_input)
        except Exception as error:
            self._record_task_failure(
                run_id=run_id,
                error=error,
                context_pack=context_json,
            )
            raise
        return {
            "run_id": run_id,
            "user_id": task_input.user_id,
            "request": task_input.request,
            "task_type": route.task_type.value,
            "selected_subgraph": "agentic_harness",
            "route": route.to_dict(),
            "status": status,
            "context_pack": context_json,
            "result": result,
            "trace": [],
            "diagnostics": diagnostics,
            "image_endpoint_template": "/items/{item_id}/image",
            "agents": {"harness": "styleforge_harness"},
            "agentic_outcome": outcome,
            "llm_enabled": True,
            "llm_call_count": harness.model_calls,
        }

    def _agentic_extension_to_result(
        self,
        task_input: TaskExecutionInput,
        outcome: dict[str, Any],
    ) -> dict[str, Any]:
        """Wrap one Harness outcome into the legacy extension task result contract.

        Three paths, mirroring the legacy chain's own statuses:
          * ``extension_result.status == needs_clarification`` — the closing node
            shipped a contract-shaped clarification (question inside the result);
          * Main-Graph NEEDS_CLARIFICATION — a subgraph (agent/coordinator)
            suspended and the ClarificationNode surfaced the question;
          * otherwise — the validated task contract, verbatim (the front end's
            ``payload.result`` shape is unchanged).
        """
        del task_input
        ext = outcome.get("extension_result") or {}
        if ext.get("status") == "needs_clarification":
            result = dict(ext.get("result") or {})
            return {
                "status": "needs_clarification",
                "message": result.get("clarification_question") or "需要补充信息后才能继续。",
                **result,
            }
        if outcome.get("status") == "needs_clarification":
            question = outcome.get("clarification_question") or "需要补充信息后才能继续。"
            return {
                "status": "needs_clarification",
                "message": question,
                "clarification_question": question,
            }
        if ext:
            return {
                "status": ext.get("status", "infeasible"),
                **(ext.get("result") or {}),
            }
        return {"status": "infeasible"}

    def _agentic_recommend_to_result(
        self,
        task_input: TaskExecutionInput,
        outcome: dict[str, Any],
        item_type_by_id: dict[str, str] | None,
        weather_facts: Any | None = None,
        llm_call_count: int = 0,
        evaluation_weights: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Wrap one Harness outcome into the recommend result contract.

        Every StageCandidate entry becomes one recommendation; the batch
        completes when at least one survives. ``needs_clarification`` surfaces
        the Coordinator / Research clarification question, otherwise infeasible.
        """
        item_type_by_id = item_type_by_id or {}
        evidence = outcome.get("research_evidence")
        recommendations: list[dict[str, Any]] = []
        skipped = 0
        for candidate in outcome.get("candidates") or []:
            item_ids = list(candidate.get("item_ids") or [])
            if len(item_ids) < 2 or not _complete_outfit_slots(
                item_ids, item_type_by_id
            ):
                skipped += 1
                continue
            outfit_id = f"rec-{uuid.uuid4().hex[:8]}"
            slot_items: dict[str, str] = {}
            for item_id in item_ids:
                slot = infer_slot(str(item_type_by_id.get(item_id, "")))
                if slot and slot != "other":
                    slot_items[slot] = item_id
            score = self._score_harness_candidate(candidate, evaluation_weights)
            acceptance_status = str(candidate.get("status") or "STAGED")
            recommendations.append(
                {
                    "outfit_id": outfit_id,
                    "item_ids": item_ids,
                    "slot_items": slot_items,
                    "hard_valid": bool(candidate.get("environment_valid", False)),
                    "score": score["score"],
                    "llm_score": score["llm_score"],
                    "score_details": score["score_details"],
                    "dimension_scores": score["dimension_scores"],
                    "evaluation_weights": score["evaluation_weights"],
                    "score_source": score["score_source"],
                    "acceptance_status": acceptance_status,
                    "degraded_reason": self._candidate_degraded_reason(candidate),
                    "reasons": self._harness_recommend_reasons(candidate, evidence),
                }
            )
        recommendations.sort(key=lambda recommendation: -recommendation["score"])
        base = {
            "environment_context": (
                {"weather": weather_facts.model_dump(mode="json")}
                if weather_facts is not None
                else {}
            ),
            "llm_enabled": True,
            "llm_call_count": llm_call_count,
        }
        if not recommendations:
            if str(outcome.get("status")) == "needs_clarification":
                question = str(outcome.get("clarification_question") or "需要你进一步说明")
                return {
                    **base,
                    "status": "needs_clarification",
                    "message": question,
                    "clarification_question": question,
                    "structured_result": {
                        "run_id": "",
                        "status": "needs_clarification",
                        "recommendations": [],
                    },
                }
            return {
                **base,
                "status": "infeasible",
                "message": "推荐失败，请换个说法重试",
                "structured_result": {
                    "run_id": "",
                    "status": "infeasible",
                    "recommendations": [],
                },
            }
        detail = f"已为你搭配 {len(recommendations)} 套方案"
        if skipped:
            detail += f"，另有 {skipped} 套候选单品过少已跳过"
        degraded_count = sum(
            recommendation["acceptance_status"] == "DEGRADED_ACCEPTED"
            for recommendation in recommendations
        )
        if degraded_count:
            detail += f"；其中 {degraded_count} 套为审校重试后保留的降级候选"
        return {
            **base,
            "status": "completed",
            "message": detail,
            "structured_result": {
                "run_id": "",
                "status": "completed",
                "recommendations": recommendations,
            },
        }

    @staticmethod
    def _evaluation_weights(context: ContextPack) -> dict[str, Any]:
        preferences = context.user_context.preferences or {}
        profile = preferences.get("evaluation_profile") or {}
        weights = profile.get("weights") if isinstance(profile, dict) else None
        return dict(weights) if isinstance(weights, dict) else {}

    @staticmethod
    def _score_harness_candidate(
        candidate: dict[str, Any],
        evaluation_weights: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Aggregate one staged Critic review with an explicit neutral fallback."""
        review = candidate.get("review") or {}
        if hasattr(review, "model_dump"):
            review = review.model_dump(mode="json")
        raw_scores = review.get("dimension_scores") if isinstance(review, dict) else None
        if hasattr(raw_scores, "model_dump"):
            raw_scores = raw_scores.model_dump(mode="json")

        keys = dimension_keys()
        critic_scored = isinstance(raw_scores, dict) and all(
            isinstance(raw_scores.get(key), (int, float)) for key in keys
        )
        if critic_scored:
            dimension_scores = {
                key: int(max(1, min(10, round(float(raw_scores[key]))))) for key in keys
            }
            score_source = "critic"
        else:
            dimension_scores = {key: 5 for key in keys}
            score_source = "neutral_fallback"

        weights = normalize_weights(evaluation_weights)
        score = aggregate_score(dimension_scores, weights)
        score_details: dict[str, Any] = {
            **{key: float(value) for key, value in dimension_scores.items()},
            **{f"weight_{key}": value for key, value in weights.items()},
            "critic_scored": 1.0 if critic_scored else 0.0,
            "degraded": (1.0 if str(candidate.get("status")) == "DEGRADED_ACCEPTED" else 0.0),
        }
        return {
            "score": score,
            "llm_score": score if critic_scored else None,
            "score_details": score_details,
            "dimension_scores": dimension_scores,
            "evaluation_weights": weights,
            "score_source": score_source,
        }

    @staticmethod
    def _candidate_degraded_reason(candidate: dict[str, Any]) -> str:
        if str(candidate.get("status")) != "DEGRADED_ACCEPTED":
            return ""
        review = candidate.get("review") or {}
        if hasattr(review, "model_dump"):
            review = review.model_dump(mode="json")
        if not isinstance(review, dict):
            return "审校重试达到上限"
        feedback = str(review.get("feedback") or "").strip()
        issues = [str(issue) for issue in review.get("issues") or [] if str(issue)]
        return feedback or "；".join(issues) or "审校重试达到上限"

    @staticmethod
    def _harness_recommend_reasons(
        candidate: dict[str, Any],
        evidence: Any,
    ) -> list[str]:
        """Human-readable rationale for one Harness recommendation.

        The candidate's own Stylist reasoning, then the shared ResearchEvidence
        facts the Stylist grounded on (dress context / theme / uncertainties) —
        the "联网搜索查到 xx → 考虑主题 → 搭配 xx" narrative the front end shows.
        """
        reasons: list[str] = []
        outfit = candidate.get("outfit")
        reasoning = str((outfit.reasoning if outfit is not None else None) or "").strip()
        if reasoning:
            reasons.append(reasoning[:240])
        item_facts: list[str] = []
        for item in (getattr(outfit, "items", None) or []):
            name = str(getattr(item, "name", "") or getattr(item, "item_type", "") or "单品")
            slot = infer_slot(str(getattr(item, "item_type", "") or ""))
            color = str(getattr(item, "color", "") or "").strip().lower()
            features = [str(value) for value in getattr(item, "features", None) or []]
            details = [slot]
            if color and color != "unknown":
                details.append(color)
            if features:
                details.append("/".join(features[:3]))
            item_facts.append(f"{name}（{'，'.join(details)}）")
        if item_facts:
            grounded = "本套实际使用的衣柜单品：" + "；".join(item_facts)
            if grounded not in reasons:
                reasons.append(grounded[:240])
        if evidence is not None:
            for ctx in getattr(evidence, "dress_context", None) or []:
                if ctx and ctx not in reasons:
                    reasons.append(ctx[:240])
            for theme in getattr(evidence, "theme_elements", None) or []:
                text = f"主题：{theme}"
                if text not in reasons:
                    reasons.append(text[:240])
            for uncertainty in getattr(evidence, "uncertainties", None) or []:
                text = f"（未确认）{uncertainty}"
                if text not in reasons:
                    reasons.append(text[:240])
        return reasons

    def _persist_agentic_recommend(
        self,
        task_input: TaskExecutionInput,
        result: dict[str, Any],
    ) -> None:
        """Commit each completed recommendation so a later turn can re-anchor.

        A subsequent ``在此基础上修改`` sends the chosen outfit_id back as
        ``current_outfit_id``; ``resolve_active_outfit`` resolves it via
        candidate_outfits JOIN styling_runs, so each agentic recommendation
        must land there for multi-turn modification to keep grounding.
        """
        structured = result.get("structured_result") or {}
        recommendations = structured.get("recommendations") or []
        candidates: list[OutfitCandidate] = []
        for recommendation in recommendations:
            item_ids = list(recommendation.get("item_ids") or [])
            outfit_id = str(recommendation.get("outfit_id", ""))
            if len(item_ids) < 2 or not outfit_id:
                continue
            candidates.append(
                OutfitCandidate(
                    outfit_id=outfit_id,
                    item_ids=tuple(item_ids),
                    slot_items=dict(recommendation.get("slot_items") or {}),
                    hard_valid=bool(recommendation.get("hard_valid", False)),
                    score=float(recommendation.get("score") or 0.0),
                    llm_score=(
                        float(recommendation["llm_score"])
                        if recommendation.get("llm_score") is not None
                        else None
                    ),
                    score_details=dict(recommendation.get("score_details") or {}),
                    reasons=tuple(recommendation.get("reasons") or []),
                )
            )
        if not candidates:
            return
        with database_session(self.database_path) as connection:
            styling_run_id = start_run(
                connection, TaskSpec(user_id=task_input.user_id, max_results=3)
            )
            save_candidates(connection, styling_run_id, candidates)
            finish_run(
                connection,
                RecommendationResult(
                    run_id=styling_run_id,
                    status="completed",
                    recommendations=tuple(candidates),
                ),
            )

    def _agentic_targets(
        self,
        task_input: TaskExecutionInput,
        session_context: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Resolve the outfit(s) a modify request applies to.

        An explicit ``current_outfit_id`` is a single explicit target (the user
        picked a card / the front end pinned it). Without one, the session's
        recent recommendation batch (>= 2 candidates) becomes the target list —
        "modify all three". Anything else degrades to the single current outfit
        (possibly empty; the loop then asks for clarification).
        """
        if task_input.current_outfit_id:
            item_ids = list(task_input.current_item_ids)
            if not item_ids:
                session = session_context or {}
                if str(session.get("current_outfit_id") or "") == task_input.current_outfit_id:
                    item_ids = list(session.get("current_item_ids") or [])
                if not item_ids:
                    for candidate in session.get("current_candidates") or []:
                        if str(candidate.get("outfit_id") or "") == task_input.current_outfit_id:
                            item_ids = list(candidate.get("item_ids") or [])
                            break
            return [
                {
                    "outfit_id": task_input.current_outfit_id,
                    "item_ids": item_ids,
                }
            ]
        candidates = (session_context or {}).get("current_candidates")
        if isinstance(candidates, list) and candidates:
            # A non-empty batch is the target set whether it holds one outfit
            # (single) or several ("modify all three").
            return [
                {
                    "outfit_id": str(candidate.get("outfit_id", "")),
                    "item_ids": list(candidate.get("item_ids", [])),
                }
                for candidate in candidates
            ]
        # Single-target fallback: an explicit request outfit, else the session's
        # current outfit (the same anchor the legacy chain would inject).
        return [
            {
                "outfit_id": task_input.current_outfit_id
                or (session_context or {}).get("current_outfit_id", ""),
                "item_ids": list(task_input.current_item_ids)
                or list((session_context or {}).get("current_item_ids", [])),
            }
        ]

    def _persist_agentic_candidate(
        self,
        task_input: TaskExecutionInput,
        result: dict[str, Any],
    ) -> None:
        """Commit the completed candidate so a later turn can re-anchor on it.

        resolve_active_outfit resolves ``current_outfit_id`` via
        candidate_outfits JOIN styling_runs, so the primary chain must persist
        here for multi-turn modifications to keep grounding on the latest outfit
        (the front end re-sends the new outfit_id as current_outfit_id).
        """
        alternatives = result.get("alternatives") or []
        candidates: list[OutfitCandidate] = []
        for alternative in alternatives:
            item_ids = list(alternative.get("item_ids") or [])
            outfit_id = str(alternative.get("outfit_id", ""))
            if len(item_ids) < 2 or not outfit_id:
                continue
            candidates.append(
                OutfitCandidate(
                    outfit_id=outfit_id,
                    item_ids=tuple(item_ids),
                    slot_items={},
                    hard_valid=bool(alternative.get("hard_valid", False)),
                    score=float(alternative.get("score") or 0.0),
                    llm_score=(
                        float(alternative["llm_score"])
                        if alternative.get("llm_score") is not None
                        else None
                    ),
                    score_details=dict(alternative.get("score_details") or {}),
                )
            )
        if not candidates:
            return
        with database_session(self.database_path) as connection:
            styling_run_id = start_run(
                connection, TaskSpec(user_id=task_input.user_id, max_results=3)
            )
            save_candidates(connection, styling_run_id, candidates)
            finish_run(
                connection,
                RecommendationResult(
                    run_id=styling_run_id,
                    status="completed",
                    recommendations=tuple(candidates),
                ),
            )
