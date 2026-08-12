"""Route all requests and execute extension tasks through the shared three agents."""

from __future__ import annotations

import re
from dataclasses import replace as dataclass_replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from styleforge.agents.composer import ComposerAgent
from styleforge.agents.critic import CriticAgent
from styleforge.agents.semantic_retriever import SemanticRetrieverAgent
from styleforge.context.builder import ContextPackBuilder
from styleforge.core.request_parser import parse_request
from styleforge.llm.client import LlmSchemaViolation
from styleforge.models.agent_tasks import Agent1TaskOutput, Agent2TaskOutput, Agent3TaskOutput
from styleforge.models.context import ContextPack
from styleforge.models.task import TaskExecutionInput
from styleforge.orchestration.task_router import TaskRoute, TaskRouter, TaskType
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.memory_repository import upsert_auto_memories
from styleforge.repositories.task_run_repository import (
    fail_task_run,
    finish_task_run,
    start_task_run,
)
from styleforge.repositories.wardrobe_repository import list_items
from styleforge.services.memory_extractor import extract_memories
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
        database_path: Path,
        knowledge_root: Path,
        llm_client: Any | None,
        recommendation_runner: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.database_path = database_path.resolve()
        self.knowledge_root = knowledge_root.resolve()
        self.llm_client = llm_client
        self.recommendation_runner = recommendation_runner
        self.router = TaskRouter()
        self.context_builder = ContextPackBuilder(self.database_path)
        self.semantic_retriever = SemanticRetrieverAgent()
        self.composer = ComposerAgent()
        self.critic = CriticAgent()
        initialize_database(self.database_path)
        self.graph = self._build_graph()

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
        output, info, _ = self.semantic_retriever.run_extension(
            database_path=self.database_path,
            knowledge_root=self.knowledge_root,
            task_input=state["task_input"],
            route=state["route"],
            context_pack=state["context_pack"],
            llm=self.llm_client,
        )
        context_pack = self._enrich_context(state["context_pack"], output)
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
        output, info, _ = self.composer.run_extension(
            user_query=state["task_input"].request,
            context_pack=state["context_pack"],
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

    def _agent3_node(self, state: TaskWorkflowState) -> dict[str, Any]:
        task_input = state["task_input"]
        with database_session(self.database_path) as connection:
            wardrobe_ids = {
                item.item_id for item in list_items(connection, task_input.user_id)
            }
        output, info, _ = self.critic.run_extension(
            user_query=task_input.request,
            context_pack=state["context_pack"],
            agent1_output=state["agent1_output"],
            agent2_output=state["agent2_output"],
            wardrobe_ids=wardrobe_ids,
            llm=self.llm_client,
        )
        diagnostics = self._merge_diagnostics(state, "agent3_initial", info)
        llm_call_count = state.get("llm_call_count", 0) + 1
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
        final_agent2 = state["agent2_output"]
        if not output.approved or not output.grounded:
            feedback = "；".join(output.issues) or output.summary or "草稿未通过语义审校"
            final_agent2, composer_info, _ = self.composer.run_extension(
                user_query=task_input.request,
                context_pack=state["context_pack"],
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
                context_pack=state["context_pack"],
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
        if route0.task_type is TaskType.OUTFIT_RECOMMEND and is_follow_up(task_input.request):
            task_input.current_outfit_id = session_outfit_id
            task_input.current_item_ids = session_item_ids
            route = self.router.route(
                task_input.request,
                current_outfit_id=session_outfit_id,
            )
            return dataclass_replace(route, reason="session_follow_up", confidence=0.9)
        if route0.task_type is TaskType.OUTFIT_MODIFY:
            task_input.current_outfit_id = session_outfit_id
            task_input.current_item_ids = session_item_ids
        return route0

    def _extract_memories(self, task_input: TaskExecutionInput) -> None:
        """Distill preference memories from the request; failures are swallowed."""
        try:
            extracts = extract_memories(task_input.request)
            if not extracts:
                return
            with database_session(self.database_path) as connection:
                upsert_auto_memories(connection, task_input.user_id, extracts)
        except Exception:
            # Memory extraction is best-effort and must never fail a task run.
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
        return payload
