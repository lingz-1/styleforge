"""Coordinator subgraph: a manager-agent that hands off, never builds outfits.

The Coordinator maintains ``TaskState`` and decides the next agent (frozen #5).
Its own loop is small — only the plan-update turn routes back to it:

    need_user        → RETURN AgentHandoffResult{NEEDS_CLARIFICATION, question}
    need_plan_update → one update_plan call → back to Coordinator → NEXT turn hands off
    otherwise        → write task_state{goal, next_agent} → RETURN COMPLETED

The parent (Main Graph) routes the COMPLETED handoff on ``task_state.next_agent``
(STYLIST / RESEARCH) — the Coordinator's whole job is that TaskState. Like every
other subgraph it only produces; the Main Graph owns the candidate chain, the
ClarificationNode and all gates. Private trajectory never crosses the boundary
(frozen #9) and the done flag is private so a stale parent ``handoff_result``
cannot end a fresh run early.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from styleforge.agentic.agentic_contract import (
    AgentHandoffResult,
    CoordinatorDecision,
    TaskState,
)
from styleforge.agentic.runtime.agent_runtime import AgentRuntime, ContextLimitError

# Manager loop is naturally short: plan updates are at most a few per request.
MAX_COORDINATOR_STEPS = 4


class CoordinatorState(TypedDict, total=False):
    """Subgraph state. Shared channels flow to/from the Main Graph; the private
    trajectory stays inside and is dropped on RETURN."""

    # context sources (input from the Main Graph) + shared products written back
    request: str
    goal: str
    user_intent: Any
    plan: Any
    task_state: TaskState | None
    research_evidence: Any
    candidates: list[Any]
    thread_context: dict | None
    recalled_memories: list[Any]
    loaded_skills: list[str]
    interaction: Any
    handoff_result: AgentHandoffResult | None
    # private trajectory — never returned to the parent (frozen #9)
    tool_observations: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    trajectory_step_count: int
    trajectory_protocol_errors: int
    pending_tools: list[dict[str, Any]]
    trajectory_done: bool


def build_coordinator_subgraph(runtime: AgentRuntime):
    """Compile the Coordinator subgraph over one AgentRuntime."""

    def _envelope(status: str, trace: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
        return {
            "handoff_result": AgentHandoffResult(
                status=status,
                trace_summary=_trace_summary(trace),
                **extra,
            ),
            "trajectory_done": True,
        }

    def coordinator_agent(state: CoordinatorState) -> dict[str, Any]:
        if state.get("trajectory_step_count", 0) >= MAX_COORDINATOR_STEPS:
            return _envelope("PROTOCOL_ERROR", state.get("trace", []))
        # Frozen #21 hard cap: re-entry after a protocol error is bounded. A
        # real provider can get stuck in a prose streak for a few turns; a valid
        # turn (below) resets the counter, so the budget bounds CONSECUTIVE
        # failures (≤ 5 in a row) — enough for DeepSeek's occasional narration
        # mode to break, never an infinite loop.
        if state.get("trajectory_protocol_errors", 0) > 5:
            return _envelope("PROTOCOL_ERROR", state.get("trace", []))
        try:
            result = runtime.call(
                "coordinator",
                dict(state),
                decision_model=CoordinatorDecision,
            )
        except ContextLimitError:
            return _envelope("PROTOCOL_ERROR", state.get("trace", []))

        if result.protocol_error is not None:
            return {
                "trajectory_protocol_errors": state.get("trajectory_protocol_errors", 0) + 1,
                "tool_observations": state.get("tool_observations", [])
                + [{
                    "tool": "__protocol__",
                    "observation": result.protocol_error,
                    "error_code": result.error_code,
                    "retryable": result.retryable,
                }],
            }

        trace = state.get("trace", []) + [result.trace]
        decision: CoordinatorDecision = result.decision
        intent_update = (
            {
                "user_intent": decision.intent.model_copy(
                    update={"message": str(state.get("request") or decision.intent.message)}
                )
            }
            if decision.intent is not None
            else {}
        )
        # A valid turn resets the protocol-error budget (frozen #21 bounds
        # CONSECUTIVE re-entries, not the lifetime total).
        if result.tool_uses:
            # need_plan_update mode: update_plan call(s), back to us.
            return {
                **intent_update,
                "pending_tools": [
                    {"name": item.name, "arguments": item.arguments}
                    for item in result.tool_uses
                ],
                "trace": trace,
                "trajectory_protocol_errors": 0,
            }

        if decision.need_user:
            return {
                **intent_update,
                "handoff_result": AgentHandoffResult(
                    status="NEEDS_CLARIFICATION",
                    clarification=decision.clarification,
                    trace_summary=_trace_summary(trace),
                ),
                "trace": trace,
                "trajectory_done": True,
                "trajectory_protocol_errors": 0,
            }
        # Handoff mode: the TaskState IS the product — the parent routes on it.
        return {
            **intent_update,
            "handoff_result": AgentHandoffResult(
                status="COMPLETED",
                trace_summary=_trace_summary(trace),
            ),
            "goal": decision.goal,
            "task_state": TaskState(goal=decision.goal, next_agent=decision.next_agent),
            "trace": trace,
            "trajectory_done": True,
            "trajectory_protocol_errors": 0,
        }

    def tool_step(state: CoordinatorState) -> dict[str, Any]:
        remaining = list(state.get("pending_tools") or [])
        pending = remaining.pop(0)
        tool_result = runtime.execute_tool(
            pending["name"],
            pending["arguments"],
            state=dict(state),
        )
        updates: dict[str, Any] = {
            "tool_observations": state.get("tool_observations", [])
            + [{
                "tool": pending["name"],
                "observation": tool_result.observation,
                "error_code": tool_result.error_code,
                "retryable": tool_result.retryable,
            }],
            "trajectory_step_count": state.get("trajectory_step_count", 0) + 1,
            "pending_tools": remaining,
        }
        updates.update(tool_result.state_updates or {})  # update_plan → new plan
        return updates

    def _route(state: CoordinatorState) -> str:
        if state.get("trajectory_done"):
            return END
        if state.get("pending_tools"):
            return "tool_step"
        return "coordinator_agent"

    builder = StateGraph(CoordinatorState)
    builder.add_node("coordinator_agent", coordinator_agent)
    builder.add_node("tool_step", tool_step)
    builder.add_edge(START, "coordinator_agent")
    builder.add_conditional_edges(
        "coordinator_agent",
        _route,
        {"tool_step": "tool_step", "coordinator_agent": "coordinator_agent", END: END},
    )
    builder.add_edge("tool_step", "coordinator_agent")
    return builder.compile()


def _trace_summary(trace: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "agent": "coordinator",
        "turns": len(trace),
        "decisions": [t.get("decision_summary", "") for t in trace[-3:]],
    }
