"""Route all requests and execute extension tasks through the shared three agents."""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import replace as dataclass_replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from styleforge.agentic.agent import AgentLoop, LoopConfig
from styleforge.agentic.environment import Environment, build_facts
from styleforge.agentic.shadow import AgenticShadowRunner, shadow_enabled, shadow_exposed
from styleforge.agents.composer import ComposerAgent
from styleforge.agents.critic import CriticAgent
from styleforge.agents.semantic_retriever import SemanticRetrieverAgent
from styleforge.context.builder import ContextPackBuilder
from styleforge.core.categories import infer_slot
from styleforge.core.request_parser import parse_request
from styleforge.core.schemas import OutfitCandidate, RecommendationResult, TaskSpec
from styleforge.knowledge.retriever import KnowledgeRetriever
from styleforge.llm.client import LlmSchemaViolation, LlmUnavailable
from styleforge.models.agent_tasks import Agent1TaskOutput, Agent2TaskOutput, Agent3TaskOutput
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
from styleforge.repositories.wardrobe_repository import list_items
from styleforge.services.memory_aggregator import apply_evidence
from styleforge.services.memory_evidence import extract_language_evidence, record_and_fold
from styleforge.services.memory_resolver import (
    AGENT_COMPOSER,
    AGENT_CRITIC,
    AGENT_RETRIEVER,
    resolve,
)
from styleforge.services.presentation import present_result
from styleforge.services.recommendation import recommend_for_user

_FOLLOW_UP_ADJUST_WORDS = (
    "更", "再", "别", "不", "一点", "太", "有点", "调整", "改变",
    "换成", "换", "改", "替换", "色系", "风格", "正式", "休闲", "简约", "商务", "酷", "花",
)
_FRESH_SCENARIO_WORDS = (
    "推荐", "面试", "聚会", "约会", "通勤", "上班", "旅行", "婚礼",
    "出席", "晚宴", "周末", "今天", "明天", "穿什么", "搭配", "选一套",
)


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


_MODIFY_MODES = ("agentic", "legacy", "shadow")

# Body regions the Agent writes in placement -> the legacy slot word the front
# end shows (``target_slot``). Best-effort mapping only; empty when unknown.
_REGION_TO_SLOT = {
    "upper_body": "top",
    "lower_body": "bottom",
    "feet": "footwear",
    "full_body": "dress",
    "accessory": "accessory",
}


def _resolve_modify_mode(modify_mode: str | None) -> str:
    """Resolve which OUTFIT_MODIFY chain is primary.

    ``agentic`` (default) runs the Agent loop as the primary chain and skips the
    legacy graph entirely; ``legacy`` keeps the old three-agent chain; ``shadow``
    keeps the legacy chain primary with the agent loop as a non-committing
    shadow. An explicit argument wins; otherwise the ``STYLEFORGE_MODIFY_MODE``
    env flag is read; anything unrecognised falls back to ``agentic``.
    """
    mode = (modify_mode or os.environ.get("STYLEFORGE_MODIFY_MODE", "")).strip().lower()
    return mode if mode in _MODIFY_MODES else "agentic"


class TaskWorkflowState(TypedDict, total=False):
    task_input: TaskExecutionInput
    route: TaskRoute
    context_pack: ContextPack
    agent1_output: Agent1TaskOutput
    agent2_output: Agent2TaskOutput
    agent3_output: Agent3TaskOutput
    result: dict[str, Any]
    status: str
    trace: list[dict[str, Any]]
    diagnostics: dict[str, Any]
    llm_call_count: int
    session_signals: dict[str, Any]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trace(state: TaskWorkflowState, node: str, **details: Any) -> list[dict[str, Any]]:
    return [
        *state.get("trace", []),
        {"node": node, "timestamp": _now(), **details},
    ]


class MultiTaskWorkflow:
    """One router plus the same strict Agent 1/2/3 chain for five extensions."""

    def __init__(
        self,
        *,
        database_path: str,
        knowledge_root: Path,
        llm_client: Any | None,
        recommendation_runner: Callable[..., dict[str, Any]] | None = None,
        chroma_store: Any | None = None,
        text_embedder: Any | None = None,
        # Stage 2 shadow mode. ``agentic_shadow`` runs the loop alongside the
        # legacy path; ``expose_agentic_shadow`` additionally attaches the
        # outcome to the API payload (tests / dev only). Both default to their
        # env flags (AGENTIC_SHADOW / AGENTIC_SHADOW_EXPOSE).
        agentic_shadow: bool | None = None,
        expose_agentic_shadow: bool | None = None,
        # Stage 4: which OUTFIT_MODIFY chain is primary. ``agentic`` (default)
        # runs the Agent loop as the primary chain (legacy graph skipped);
        # ``legacy`` keeps the old three-agent chain; ``shadow`` keeps legacy
        # primary with the agent loop alongside. Defaults to the
        # STYLEFORGE_MODIFY_MODE env flag.
        modify_mode: str | None = None,
        # Web search for the agentic ``search_web`` tool. None degrades the
        # tool to an "unconfigured" observation; the loop keeps working.
        web_search_provider: Any | None = None,
    ) -> None:
        self.database_path = database_path
        self.knowledge_root = knowledge_root.resolve()
        self.llm_client = llm_client
        self.web_search_provider = web_search_provider
        self.recommendation_runner = recommendation_runner
        self.router = TaskRouter()
        self.context_builder = ContextPackBuilder(self.database_path)
        self.semantic_retriever = SemanticRetrieverAgent()
        self.composer = ComposerAgent()
        self.critic = CriticAgent()
        # Shared retriever; vector retrieval is additive and degrades to
        # keyword-only when Chroma/model loading fails.
        self.knowledge_retriever = KnowledgeRetriever(
            self.knowledge_root,
            chroma_store=chroma_store,
            text_embedder=text_embedder,
        )
        initialize_database(self.database_path)
        self.graph = self._build_graph()
        self.agentic_shadow_enabled = (
            agentic_shadow if agentic_shadow is not None else shadow_enabled()
        )
        self.expose_agentic_shadow = (
            expose_agentic_shadow
            if expose_agentic_shadow is not None
            else shadow_exposed()
        )
        self.modify_mode = _resolve_modify_mode(modify_mode)
        self.agentic_shadow_runner = AgenticShadowRunner(
            database_path,
            llm_client,
            enabled=self.agentic_shadow_enabled,
            web_search_provider=self.web_search_provider,
        )

    def _build_graph(self):
        builder = StateGraph(TaskWorkflowState)
        builder.add_node("task_router", self._route_node)
        builder.add_node("recommendation_runner", self._recommendation_node)
        builder.add_node("semantic_retriever_agent", self._agent1_node)
        builder.add_node("composer_agent", self._agent2_node)
        builder.add_node("critic_agent", self._agent3_node)
        builder.add_edge(START, "task_router")
        builder.add_conditional_edges(
            "task_router",
            self._after_route,
            {
                "recommendation_runner": "recommendation_runner",
                "semantic_retriever_agent": "semantic_retriever_agent",
            },
        )
        builder.add_edge("recommendation_runner", END)
        builder.add_edge("semantic_retriever_agent", "composer_agent")
        builder.add_edge("composer_agent", "critic_agent")
        builder.add_edge("critic_agent", END)
        return builder.compile()

    def _route_node(self, state: TaskWorkflowState) -> dict[str, Any]:
        task_input = state["task_input"]
        route = state.get("route") or self.router.route(
            task_input.request,
            current_outfit_id=task_input.current_outfit_id,
            has_candidate_item=task_input.candidate_item is not None,
            requested_task_type=task_input.requested_task_type,
        )
        context_pack = self.context_builder.build(task_input, route)
        return {
            "route": route,
            "context_pack": context_pack,
            "status": "routed",
            "trace": _trace(
                state,
                "task_router",
                task_type=route.task_type.value,
                selected_subgraph=route.subgraph,
                reason=route.reason,
            ),
        }

    @staticmethod
    def _after_route(state: TaskWorkflowState) -> str:
        if state["route"].task_type is TaskType.OUTFIT_RECOMMEND:
            return "recommendation_runner"
        return "semantic_retriever_agent"

    def _recommendation_node(self, state: TaskWorkflowState) -> dict[str, Any]:
        task_input = state["task_input"]
        if self.recommendation_runner is not None:
            result = self.recommendation_runner(
                user_id=task_input.user_id,
                request=task_input.request,
                max_results=task_input.max_results,
                location_context=(
                    task_input.location_context.model_dump(exclude_none=True)
                    if task_input.location_context is not None
                    else None
                ),
            )
            status = str(result.get("structured_result", {}).get("status", "completed"))
        else:
            parsed = parse_request(
                task_input.user_id,
                task_input.request,
                task_input.max_results,
            )
            recommendation = recommend_for_user(self.database_path, parsed)
            result = present_result(self.database_path, recommendation)
            status = recommendation.status
        context_pack = state["context_pack"].model_copy(deep=True)
        if isinstance(result, dict):
            environment = result.get("environment_context") or {}
            if isinstance(environment, dict):
                context_pack.environment_context.weather = environment.get("weather")
        return {
            "result": result,
            "context_pack": context_pack,
            "status": status,
            "trace": _trace(state, "recommendation_runner", status=status),
        }

    @staticmethod
    def _merge_diagnostics(
        state: TaskWorkflowState,
        agent_name: str,
        info: dict[str, Any],
    ) -> dict[str, Any]:
        return {**state.get("diagnostics", {}), agent_name: info}

    @staticmethod
    def _enrich_context(
        context_pack: ContextPack,
        output: Agent1TaskOutput,
    ) -> ContextPack:
        context_pack = context_pack.model_copy(deep=True)
        if output.task_type in {TaskType.STYLE_ADVICE, TaskType.WARDROBE_GAP}:
            context_pack.knowledge_context.style_guides = list(output.evidence)
        if output.task_type is TaskType.WARDROBE_GAP:
            context_pack.knowledge_context.wardrobe_analysis = list(output.evidence)
        if output.task_type in {
            TaskType.ITEM_ADVICE,
            TaskType.WARDROBE_COMPATIBILITY,
        }:
            context_pack.knowledge_context.item_guides = list(output.evidence)
        if output.task_type is TaskType.OUTFIT_MODIFY:
            facts = output.facts
            context_pack.outfit_context.current_outfit_id = str(
                facts.get("current_outfit_id", "")
            )
            context_pack.outfit_context.current_item_ids = list(
                facts.get("current_item_ids", [])
            )
            context_pack.outfit_context.locked_item_ids = list(
                facts.get("locked_item_ids", [])
            )
            target_slot = str(output.resolved_target.get("target_slot", ""))
            context_pack.outfit_context.editable_slots = [target_slot] if target_slot else []
        candidate = output.facts.get("candidate_item")
        if isinstance(candidate, dict):
            context_pack.candidate_item = candidate
        return context_pack

    def _agent1_node(self, state: TaskWorkflowState) -> dict[str, Any]:
        agent_context = state["context_pack"].model_copy(deep=True)
        self._apply_memory_pack(agent_context, AGENT_RETRIEVER, state["task_input"].request, state.get("session_signals"))
        output, info, _ = self.semantic_retriever.run_extension(
            database_path=self.database_path,
            knowledge_root=self.knowledge_root,
            task_input=state["task_input"],
            route=state["route"],
            context_pack=agent_context,
            llm=self.llm_client,
            knowledge_retriever=self.knowledge_retriever,
        )
        context_pack = self._enrich_context(state["context_pack"], output)
        self._record_item_replaced(state["task_input"], output)
        return {
            "agent1_output": output,
            "context_pack": context_pack,
            "status": "agent1_completed",
            "llm_call_count": state.get("llm_call_count", 0)
            + int(info.get("call_count", 1)),
            "diagnostics": self._merge_diagnostics(state, "agent1", info),
            "trace": _trace(
                state,
                "semantic_retriever_agent",
                status="completed",
                backend="llm",
                degraded=False,
                llm_calls=int(info.get("call_count", 1)),
            ),
        }

    def _agent2_node(self, state: TaskWorkflowState) -> dict[str, Any]:
        agent_context = state["context_pack"].model_copy(deep=True)
        self._apply_memory_pack(agent_context, AGENT_COMPOSER, state["task_input"].request, state.get("session_signals"))
        output, info, _ = self.composer.run_extension(
            user_query=state["task_input"].request,
            context_pack=agent_context,
            agent1_output=state["agent1_output"],
            llm=self.llm_client,
        )
        return {
            "agent2_output": output,
            "status": "agent2_completed",
            "llm_call_count": state.get("llm_call_count", 0)
            + int(info.get("call_count", 1)),
            "diagnostics": self._merge_diagnostics(state, "agent2", info),
            "trace": _trace(
                state,
                "composer_agent",
                status="completed",
                backend="llm",
                degraded=False,
                llm_calls=int(info.get("call_count", 1)),
            ),
        }

    def _critic_with_repair(
        self,
        *,
        task_input: TaskExecutionInput,
        critic_context: ContextPack,
        composer_context: ContextPack,
        state: TaskWorkflowState,
        wardrobe_ids: set[str],
    ) -> tuple[Agent3TaskOutput, dict[str, Any], Agent2TaskOutput, int]:
        """Run Agent 3, recomposing once when hard validation fails.

        The hard validator lives inside ``critic.run_extension``; its
        ``ValueError`` (duplicate core slots, too few alternatives, …) used to
        propagate straight into a failed task run with no repair. Catch it here,
        hand the exact validator message to Agent 2 as ``repair_feedback``, and
        re-critic. Returns ``(critic_output, info, agent2, agent3_calls)`` where
        ``agent3_calls`` counts the LLM calls this phase spent: 1 on the happy
        path, ``1 + composer_calls`` when a repair recompose ran. A second
        failure propagates so a hopeless draft still surfaces as an error
        instead of looping forever.
        """
        try:
            output, info, _ = self.critic.run_extension(
                user_query=task_input.request,
                context_pack=critic_context,
                agent1_output=state["agent1_output"],
                agent2_output=state["agent2_output"],
                wardrobe_ids=wardrobe_ids,
                llm=self.llm_client,
            )
            return output, info, state["agent2_output"], 1
        except ValueError as error:
            repaired, composer_info, _ = self.composer.run_extension(
                user_query=task_input.request,
                context_pack=composer_context,
                agent1_output=state["agent1_output"],
                llm=self.llm_client,
                repair_feedback=f"上次草稿未通过硬校验：{error}",
            )
            output, retry_info, _ = self.critic.run_extension(
                user_query=task_input.request,
                context_pack=critic_context,
                agent1_output=state["agent1_output"],
                agent2_output=repaired,
                wardrobe_ids=wardrobe_ids,
                llm=self.llm_client,
            )
            return output, {
                **retry_info,
                "agent2_repair": composer_info,
            }, repaired, 1 + int(composer_info.get("call_count", 1))

    def _agent3_node(self, state: TaskWorkflowState) -> dict[str, Any]:
        task_input = state["task_input"]
        with database_session(self.database_path) as connection:
            wardrobe_ids = {
                item.item_id for item in list_items(connection, task_input.user_id)
            }
        critic_context = state["context_pack"].model_copy(deep=True)
        self._apply_memory_pack(
            critic_context, AGENT_CRITIC, task_input.request, state.get("session_signals")
        )
        composer_context = state["context_pack"].model_copy(deep=True)
        self._apply_memory_pack(
            composer_context, AGENT_COMPOSER, task_input.request, state.get("session_signals")
        )
        output, info, final_agent2, agent3_calls = self._critic_with_repair(
            task_input=task_input,
            critic_context=critic_context,
            composer_context=composer_context,
            state=state,
            wardrobe_ids=wardrobe_ids,
        )
        diagnostics = self._merge_diagnostics(state, "agent3_initial", info)
        llm_call_count = state.get("llm_call_count", 0) + agent3_calls
        trace = _trace(
            state,
            "critic_agent",
            status="completed" if output.approved and output.grounded else "rejected",
            backend="llm",
            degraded=False,
            attempt=1,
            approved=output.approved,
            grounded=output.grounded,
        )
        if not output.approved or not output.grounded:
            feedback = "；".join(output.issues) or output.summary or "草稿未通过语义审校"
            final_agent2, composer_info, _ = self.composer.run_extension(
                user_query=task_input.request,
                context_pack=composer_context,
                agent1_output=state["agent1_output"],
                llm=self.llm_client,
                critic_feedback=feedback,
            )
            composer_calls = int(composer_info.get("call_count", 1))
            llm_call_count += composer_calls
            diagnostics["agent2_recompose"] = composer_info
            trace.append(
                {
                    "node": "composer_agent",
                    "timestamp": _now(),
                    "status": "recomposed",
                    "backend": "llm",
                    "degraded": False,
                    "attempt": 2,
                    "llm_calls": composer_calls,
                    "critic_feedback": feedback,
                }
            )
            output, retry_info, _ = self.critic.run_extension(
                user_query=task_input.request,
                context_pack=critic_context,
                agent1_output=state["agent1_output"],
                agent2_output=final_agent2,
                wardrobe_ids=wardrobe_ids,
                llm=self.llm_client,
            )
            llm_call_count += 1
            diagnostics["agent3_retry"] = retry_info
            trace.append(
                {
                    "node": "critic_agent",
                    "timestamp": _now(),
                    "status": (
                        "completed" if output.approved and output.grounded else "rejected"
                    ),
                    "backend": "llm",
                    "degraded": False,
                    "attempt": 2,
                    "approved": output.approved,
                    "grounded": output.grounded,
                }
            )
            if not output.approved or not output.grounded:
                retry_feedback = (
                    "；".join(output.issues) or output.summary or "重做草稿仍未通过语义审校"
                )
                raise LlmSchemaViolation(
                    f"extension Agent 3 rejected draft after one recompose: {retry_feedback}"
                )
        return {
            "agent2_output": final_agent2,
            "agent3_output": output,
            "result": output.result,
            "status": output.status,
            "llm_call_count": llm_call_count,
            "diagnostics": diagnostics,
            "trace": trace,
        }

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
        session_item_ids = [
            item_id for item_id in session_item_ids if item_id in active_ids
        ]
        if not session_item_ids:
            return route0
        # Under the agentic modify chain the Agent loop resolves its own targets
        # from ``session_context`` (``_agentic_targets``); injecting the session
        # outfit into ``task_input`` would mask "no explicit choice" and defeat
        # "modify all three". The legacy / shadow chains still need the injection
        # to ground their context pack.
        inject_session = self.modify_mode != "agentic"
        if route0.task_type is TaskType.OUTFIT_RECOMMEND and is_follow_up(task_input.request):
            if inject_session:
                task_input.current_outfit_id = session_outfit_id
                task_input.current_item_ids = session_item_ids
            route = self.router.route(
                task_input.request,
                current_outfit_id=session_outfit_id,
            )
            return dataclass_replace(route, reason="session_follow_up", confidence=0.9)
        if route0.task_type is TaskType.OUTFIT_MODIFY:
            if inject_session:
                task_input.current_outfit_id = session_outfit_id
                task_input.current_item_ids = session_item_ids
        return route0

    def _extract_memories(self, task_input: TaskExecutionInput) -> None:
        """Distill preference evidence via LLM and aggregate it; failures swallowed."""
        try:
            evidence = extract_language_evidence(self.llm_client, task_input.request)
            if not evidence:
                return
            with database_session(self.database_path) as connection:
                apply_evidence(connection, task_input.user_id, evidence)
        except Exception:
            # Memory extraction is best-effort and must never fail a task run.
            pass

    def _apply_memory_pack(
        self,
        context_pack: ContextPack,
        agent_role: str,
        request_text: str,
        session_signals: dict[str, Any] | None = None,
    ) -> None:
        """Swap ``preferences.memory_profile`` for the agent's differentiated pack.

        The raw preference list stays in the stored context pack so each node
        re-derives its own slice; the copy passed to the agent carries only the
        buckets that agent role may see.
        """
        preferences = context_pack.user_context.preferences or {}
        if not preferences.get("memory_profile"):
            return
        pack = resolve(
            preferences["memory_profile"],
            request_signature=(
                {"practical_context": request_text} if request_text.strip() else None
            ),
            agent_role=agent_role,
            session_signals=session_signals,
        )
        context_pack.user_context.preferences = {
            **preferences,
            "memory_profile": pack,
        }

    def _record_item_replaced(
        self,
        task_input: TaskExecutionInput,
        output: Agent1TaskOutput,
    ) -> None:
        """Record an ``item_replaced`` behavior event from modification facts."""
        facts = output.facts if output.task_type is TaskType.OUTFIT_MODIFY else {}
        replaced = list(facts.get("replaced_item_ids", []) or [])
        replacement = list(facts.get("replacement_item_ids", []) or [])
        if not replaced and not replacement:
            return
        try:
            with database_session(self.database_path) as connection:
                record_and_fold(
                    connection,
                    task_input.user_id,
                    "item_replaced",
                    context={
                        "request": task_input.request,
                        "outfit_id": str(facts.get("current_outfit_id", "")),
                    },
                    features={
                        "replaced_item_ids": replaced,
                        "replacement_item_ids": replacement,
                    },
                )
        except BaseException:
            pass

    def execute(
        self,
        task_input: TaskExecutionInput,
        *,
        session_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        route = self._route_with_session(task_input, session_context)
        with database_session(self.database_path) as connection:
            run_id = start_task_run(
                connection,
                user_id=task_input.user_id,
                task_type=route.task_type,
                request=task_input.request,
            )
        initial_context = self.context_builder.build(task_input, route)
        # Stage 2 shadow: freeze the *pre-legacy* context pack so the shadow
        # sees the same world the legacy saw (logical parallelism; execution
        # may be serial). `initial_context` is never mutated by the graph.
        frozen_context = initial_context
        # Stage 4: with the Agent loop primary, OUTFIT_MODIFY never enters the
        # legacy graph — the loop is the chain, and the legacy branch (incl. its
        # shadow mount) is skipped entirely.
        if route.task_type is TaskType.OUTFIT_MODIFY and self.modify_mode == "agentic":
            return self._run_agentic_modify(
                task_input, route, initial_context, run_id, session_context
            )
        try:
            final = self.graph.invoke(
                {
                    "task_input": task_input,
                    "route": route,
                    "context_pack": initial_context,
                    "status": "running",
                    "trace": [],
                    "diagnostics": {},
                    "llm_call_count": 0,
                    "session_signals": (session_context or {}).get("session_signals") or {},
                }
            )
            context_pack = final["context_pack"].model_dump(mode="json")
            result = final["result"]
            status = final["status"]
            with database_session(self.database_path) as connection:
                finish_task_run(
                    connection,
                    run_id=run_id,
                    status=status,
                    context_pack=context_pack,
                    result=result,
                )
            self._extract_memories(task_input)
        except Exception as error:
            with database_session(self.database_path) as connection:
                fail_task_run(
                    connection,
                    run_id=run_id,
                    error=error,
                    context_pack=initial_context.model_dump(mode="json"),
                )
            raise

        payload: dict[str, Any] = {
            "run_id": run_id,
            "user_id": task_input.user_id,
            "request": task_input.request,
            "task_type": route.task_type.value,
            "selected_subgraph": route.subgraph,
            "route": route.to_dict(),
            "status": status,
            "context_pack": context_pack,
            "result": result,
            "trace": final.get("trace", []),
            "diagnostics": final.get("diagnostics", {}),
            "image_endpoint_template": "/items/{item_id}/image",
        }
        if route.task_type is TaskType.OUTFIT_RECOMMEND:
            payload["llm_enabled"] = bool(result.get("llm_enabled", False))
            payload["llm_call_count"] = int(result.get("llm_call_count", 0))
            return payload
        payload.update(
            {
                "agents": {
                    "agent1": "semantic_retriever",
                    "agent2": "composer",
                    "agent3": "critic",
                },
                "agent_outputs": {
                    "agent1": final["agent1_output"].model_dump(mode="json"),
                    "agent2": final["agent2_output"].model_dump(mode="json"),
                    "agent3": final["agent3_output"].model_dump(mode="json"),
                },
                "llm_enabled": True,
                "llm_call_count": final.get("llm_call_count", 0),
            }
        )
        if route.task_type is TaskType.OUTFIT_MODIFY:
            shadow_result = self.agentic_shadow_runner.run(
                task_input, frozen_context, session_context
            )
            if shadow_result and self.expose_agentic_shadow:
                # Test / dev only: the shadow outcome rides the payload. The
                # response contract is otherwise unchanged.
                payload["agentic_shadow"] = shadow_result
        return payload

    # --- Stage 4: agentic primary chain ----------------------------------

    def _run_agentic_modify(
        self,
        task_input: TaskExecutionInput,
        route: TaskRoute,
        initial_context: ContextPack,
        run_id: str,
        session_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run OUTFIT_MODIFY through the Agent loop as the primary chain.

        Mirrors the shadow sequence (build_facts -> Environment -> AgentLoop)
        but the outcome is the real result: it is validated against the
        OutfitModifyResult contract, committed to task_runs, and (when it
        produces a completed outfit) persisted to candidate_outfits so a later
        turn can re-anchor on it by outfit_id.

        The targets come from ``_agentic_targets``: an explicit
        ``current_outfit_id`` means one outfit; otherwise every recent
        recommendation candidate in the session context is a target ("modify
        all three"). One loop runs per target, and the batch of outcomes is
        wrapped into one result with one alternative per success.
        """
        if self.llm_client is None:
            raise LlmUnavailable("OUTFIT_MODIFY requires an LLM client")
        context_json = initial_context.model_dump(mode="json")
        targets = self._agentic_targets(task_input, session_context)
        try:
            with database_session(self.database_path) as connection:
                wardrobe_items = list_items(connection, task_input.user_id)
                item_type_by_id = {
                    item.item_id: item.item_type for item in wardrobe_items
                }
                outcomes: list[dict[str, Any]] = []
                for target in targets:
                    sub_input = task_input.model_copy(
                        update={
                            "current_outfit_id": target["outfit_id"],
                            "current_item_ids": target["item_ids"],
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
                        web_search_provider=self.web_search_provider,
                    )
                    loop = AgentLoop(
                        self.llm_client,
                        environment,
                        task_input.request,
                        config=LoopConfig(max_steps=8, search_limit=12),
                    )
                    outcomes.append(loop.run())
            result = self._agentic_outcomes_to_result(
                task_input, targets, outcomes, item_type_by_id
            )
            status = str(result.get("status", "infeasible"))
            with database_session(self.database_path) as connection:
                finish_task_run(
                    connection,
                    run_id=run_id,
                    status=status,
                    context_pack=context_json,
                    result=result,
                )
            if status == "completed":
                self._persist_agentic_candidate(task_input, result)
            self._extract_memories(task_input)
        except Exception as error:
            with database_session(self.database_path) as connection:
                fail_task_run(
                    connection,
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
            "selected_subgraph": "agentic_loop",
            "route": route.to_dict(),
            "status": status,
            "context_pack": context_json,
            "result": result,
            "trace": [],
            "diagnostics": {},
            "image_endpoint_template": "/items/{item_id}/image",
            "agents": {"loop": "agentic_agent_loop"},
            "agentic_outcome": outcomes[0] if len(outcomes) == 1 else outcomes,
            "llm_enabled": True,
            "llm_call_count": sum(
                int(outcome.get("llm_call_count", 0)) for outcome in outcomes
            ),
        }

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
            return [
                {
                    "outfit_id": task_input.current_outfit_id,
                    "item_ids": list(task_input.current_item_ids),
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

    def _agentic_outcomes_to_result(
        self,
        task_input: TaskExecutionInput,
        targets: list[dict[str, Any]],
        outcomes: list[dict[str, Any]],
        item_type_by_id: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Wrap a batch of Agent loop outcomes (one per target outfit) into the
        OutfitModifyResult contract.

        Every successful outcome becomes one alternative; the batch completes
        when at least one alternative survives. A single outcome delegates to
        the single-outfit wrapper so Stage 4's one-card behaviour is unchanged.
        """
        if len(outcomes) == 1:
            return self._agentic_outcome_to_result(
                task_input, outcomes[0], item_type_by_id
            )
        item_type_by_id = item_type_by_id or {}
        base_id = task_input.current_outfit_id or ""
        alternatives: list[dict[str, Any]] = []
        skipped = 0
        first_failure: dict[str, Any] | None = None
        first_success_outcome: dict[str, Any] | None = None
        for target, outcome in zip(targets, outcomes):
            agentic_status = str(outcome.get("status", "timeout"))
            if agentic_status != "success":
                if first_failure is None:
                    first_failure = outcome
                continue
            candidate = outcome.get("candidate") or {}
            item_ids = list(candidate.get("item_ids") or [])
            base_outfit_id = str(
                candidate.get("outfit_id") or target.get("outfit_id") or base_id
            )
            if len(item_ids) < 2:
                # OutfitReference.item_ids requires at least two; drop the
                # under-sized candidate instead of failing the whole batch.
                skipped += 1
                continue
            new_outfit_id = f"{base_outfit_id or 'outfit'}-mod-{uuid.uuid4().hex[:6]}"
            review = outcome.get("review") or {}
            intent = outcome.get("intent") or {}
            reasoning = str(
                review.get("feedback") or intent.get("goal") or "已按你的要求修改搭配"
            )
            replaced, locked = self._extract_replaced_locked(
                outcome, item_ids, list(target.get("item_ids") or [])
            )
            alternatives.append(
                {
                    "outfit_id": new_outfit_id,
                    "item_ids": item_ids,
                    "reasoning": reasoning,
                    "replaced_item_ids": replaced,
                    "locked_item_ids": locked,
                }
            )
            if first_success_outcome is None:
                first_success_outcome = outcome
        if not alternatives:
            if (
                first_failure is not None
                and str(first_failure.get("status")) == "ask_user"
            ):
                question = str(
                    (first_failure.get("ask_user") or {}).get("question")
                    or "需要你进一步说明"
                )
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
                    "message": "修改超时，请换个说法重试",
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
        assert first_success_outcome is not None
        target_slot = self._extract_target_slot(
            first_success_outcome, all_replaced, item_type_by_id
        )
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
                "locked_item_ids": all_locked,
                "alternatives": alternatives,
                "message": detail,
            },
        )

    def _agentic_outcome_to_result(
        self,
        task_input: TaskExecutionInput,
        outcome: dict[str, Any],
        item_type_by_id: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Wrap an Agent loop outcome into the OutfitModifyResult contract.

        Status mapping: success -> completed, ask_user -> needs_clarification,
        timeout -> infeasible. Only a completed outcome produces an alternative
        (a single candidate — the Stage 4 decision to show one card); the shape
        is revalidated against the contract model so legacy consumers keep a
        contract-clean result.
        """
        item_type_by_id = item_type_by_id or {}
        base_id = task_input.current_outfit_id or ""
        agentic_status = str(outcome.get("status", "timeout"))
        ask_user = outcome.get("ask_user") or {}
        review = outcome.get("review") or {}
        intent = outcome.get("intent") or {}
        candidate = outcome.get("candidate") or {}

        if agentic_status == "ask_user":
            question = str(ask_user.get("question") or "需要你进一步说明")
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

        if agentic_status != "success":
            return validate_task_result(
                TaskType.OUTFIT_MODIFY,
                {
                    "status": "infeasible",
                    "current_outfit_id": base_id,
                    "target_slot": "",
                    "replaced_item_ids": [],
                    "locked_item_ids": [],
                    "alternatives": [],
                    "message": "修改超时，请换个说法重试",
                },
            )

        item_ids = list(candidate.get("item_ids") or [])
        new_outfit_id = f"{base_id or 'outfit'}-mod-{uuid.uuid4().hex[:6]}"
        reasoning = str(
            review.get("feedback") or intent.get("goal") or "已按你的要求修改搭配"
        )
        replaced, locked = self._extract_replaced_locked(
            outcome, item_ids, task_input.current_item_ids
        )
        result = {
            "status": "completed",
            "current_outfit_id": new_outfit_id,
            "target_slot": self._extract_target_slot(
                outcome, replaced, item_type_by_id
            ),
            "replaced_item_ids": replaced,
            "locked_item_ids": locked,
            "alternatives": [
                {
                    "outfit_id": new_outfit_id,
                    "item_ids": item_ids,
                    "reasoning": reasoning,
                }
            ],
            "message": reasoning,
        }
        if len(item_ids) < 2:
            # OutfitReference.item_ids requires at least two; a one-item
            # "outfit" cannot satisfy the contract, surface it as a
            # clarification instead of failing the whole run.
            result["status"] = "needs_clarification"
            result["message"] = "当前搭配单品过少，请补充想怎么调整"
            result["alternatives"] = []
            result["clarification_question"] = result["message"]
            result["current_outfit_id"] = base_id
        return validate_task_result(TaskType.OUTFIT_MODIFY, result)

    def _extract_replaced_locked(
        self,
        outcome: dict[str, Any],
        final_item_ids: list[str],
        base_item_ids: list[str],
    ) -> tuple[list[str], list[str]]:
        """Best-effort replaced/locked ids from the loop's modify steps.

        ``replaced`` collects every item the loop removed/replaced; ``locked``
        is the intersection of the base outfit and the final one (kept as-is).
        """
        replaced: list[str] = []
        for step in outcome.get("steps", []):
            if step.get("action") != "modify_outfit":
                continue
            plan = (step.get("args") or {}).get("plan") or {}
            for op in plan.get("ops") or []:
                action = op.get("action")
                if action in ("replace", "remove"):
                    item_id = op.get("item_id")
                    if item_id and item_id not in replaced:
                        replaced.append(item_id)
        final_set = set(final_item_ids)
        locked = [item_id for item_id in base_item_ids if item_id in final_set]
        return replaced, locked

    def _extract_target_slot(
        self,
        outcome: dict[str, Any],
        replaced_item_ids: list[str],
        item_type_by_id: dict[str, str],
    ) -> str:
        """Best-effort target slot: the slot of a replaced item (by type),
        falling back to the placement region words in the modify steps."""
        for item_id in replaced_item_ids:
            item_type = item_type_by_id.get(item_id)
            if item_type:
                slot = infer_slot(item_type)
                if slot and slot != "other":
                    return slot
        for step in outcome.get("steps", []):
            if step.get("action") != "modify_outfit":
                continue
            plan = (step.get("args") or {}).get("plan") or {}
            for op in plan.get("ops") or []:
                placement = op.get("placement") or {}
                region = str(placement.get("region", ""))
                slot = _REGION_TO_SLOT.get(region)
                if slot:
                    return slot
        return ""

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
                    hard_valid=True,
                    score=0.0,
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
