"""Stylist subgraph: produce ONE candidate, never validate or persist.

The subgraph owns only the Stylist's working trajectory (frozen #9): it loops
``stylist_agent ↔ tool_step`` until the Agent signals CANDIDATE_READY, NEED_USER
or hits the protocol-error cap, then RETURNs an ``AgentHandoffResult`` envelope
(frozen #20). It never jumps to a Main-Graph node (no ClarificationNode, no
Environment Gate, no Critic inside — the subgraph only produces; the Main Graph
verifies), and it never persists anything (frozen #19: StageCandidate is a
Main-Graph node).

Channels shared with the Main Graph (context sources, working_draft, base_draft,
``handoff_result``) pass across the boundary; the private trajectory
(``tool_observations`` / ``trace`` / ``step_count`` / ``pending_tool``) stays
inside this subgraph and is dropped when the subgraph returns.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from styleforge.agentic.agentic_contract import AgentHandoffResult, StylistDecision
from styleforge.agentic.runtime.agent_runtime import AgentRuntime, ContextLimitError

# Step hard cap (safety, mirrors the legacy LoopConfig.max_steps). On exceed the
# subgraph returns PROTOCOL_ERROR as the harness-internal "did not produce" stop;
# it is NOT a fabricated candidate. 12 rather than 8: a real provider exploring
# a fresh candidate after a Critic rejection burns several tool turns before
# converging (search + build + adjust); too tight a cap strangles a productive
# run at its last step.
MAX_STYLIST_STEPS = 12

# Bounded revision (frozen #7 real-model deadlock): after a Critic / Env-Gate
# rejection (gate_feedback set) the Stylist may keep editing for at most this
# many tool turns, then MUST re-submit whatever legal draft it has. A real
# provider loops "modify → modify → ..." forever when the wardrobe cannot yield
# a direction distinct enough to satisfy the Critic; forcing the re-submit hands
# control back to the Main Graph where the bounded critic budget
# (MAX_CRITIC_RETRIES) accepts the candidate instead of burning the step cap.
# Only revision rounds are bounded — a fresh candidate from the empty base draft
# is unrestricted (MAX_STYLIST_STEPS still guards it).
MAX_REVISION_STEPS = 3


class StylistState(TypedDict, total=False):
    """Subgraph state. Shared channels flow from/to the Main Graph; private
    trajectory channels never cross back."""

    # context sources (input from the Main Graph, read-only inside the subgraph)
    request: str
    goal: str
    plan: Any
    task_state: Any
    environment_facts: Any
    research_evidence: Any
    candidates: list[Any]
    thread_context: dict | None
    recalled_memories: list[Any]
    loaded_skills: list[str]
    interaction: Any
    base_draft: Any
    gate_feedback: str | None
    # working state (shared: the candidate under construction)
    working_draft: Any
    # private trajectory — never returned to the parent (frozen #9). The
    # counters are namespaced ``trajectory_*`` so they cannot collide with the
    # parent's ``step_count`` / ``protocol_error_count`` channels: langgraph
    # merges subgraph channels by name, and a shared counter would leak one
    # candidate's step/protocol budget into the next candidate's fresh run.
    tool_observations: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    trajectory_step_count: int
    trajectory_protocol_errors: int
    revision_steps: int  # tool turns consumed in THIS bounded-revision round
    pending_tools: list[dict[str, Any]]
    # Done-in-this-run flag. MUST be private: the shared ``handoff_result``
    # channel carries the PREVIOUS run's envelope back into the subgraph as
    # input, so routing on it would end a fresh run before its first tool step.
    trajectory_done: bool
    # envelope output (shared with the parent)
    handoff_result: AgentHandoffResult | None


def build_stylist_subgraph(runtime: AgentRuntime):
    """Compile the Stylist subgraph over one AgentRuntime (runtime deps live in
    the harness instance, never in the state)."""

    def _envelope(status: str, trace: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
        return {
            "handoff_result": AgentHandoffResult(
                status=status,
                trace_summary=_trace_summary(trace),
                **extra,
            ),
            "trajectory_done": True,
        }

    def stylist_agent(state: StylistState) -> dict[str, Any]:
        if state.get("trajectory_step_count", 0) >= MAX_STYLIST_STEPS:
            return _envelope("PROTOCOL_ERROR", state.get("trace", []))
        # Frozen #21 hard cap: re-entry after a protocol error is bounded. A
        # real provider can get stuck in a prose streak for a few turns; a valid
        # turn (below) resets the counter, so the budget bounds CONSECUTIVE
        # failures (≤ 5 in a row) — enough for DeepSeek's occasional narration
        # mode to break, never an infinite loop.
        if state.get("trajectory_protocol_errors", 0) > 5:
            return _envelope("PROTOCOL_ERROR", state.get("trace", []))
        # Bounded revision: in a revision round (gate_feedback set by a rejected
        # candidate) the Stylist edits at most MAX_REVISION_STEPS turns, then
        # re-submits whatever legal draft it has — no model call, no more
        # editing. This breaks the real-model "modify forever" deadlock; the
        # Main Graph's critic budget decides accept vs. replan from there.
        if (
            state.get("gate_feedback")
            and state.get("revision_steps", 0) >= MAX_REVISION_STEPS
            and (state.get("working_draft") is not None)
            and state["working_draft"].outfit.item_ids
        ):
            trace = state.get("trace", []) + [
                {
                    "agent": "stylist",
                    "decision_summary": "已达修订步数上限，强制提交当前方案",
                    "control": "CANDIDATE_READY",
                }
            ]
            return _envelope("COMPLETED", trace)
        try:
            result = runtime.call(
                "stylist",
                dict(state),
                decision_model=StylistDecision,
            )
        except ContextLimitError:
            return _envelope("PROTOCOL_ERROR", state.get("trace", []))

        if result.protocol_error is not None:
            # Re-entry path: append the error observation, bump the cap counter,
            # let the Agent try once more.
            return {
                "trajectory_protocol_errors": state.get("trajectory_protocol_errors", 0) + 1,
                "tool_observations": state.get("tool_observations", [])
                + [{"tool": "__protocol__", "observation": result.protocol_error}],
            }

        trace = state.get("trace", []) + [result.trace]
        # A valid turn resets the protocol-error budget (frozen #21: re-entry
        # after a protocol error ≤ 1 — i.e. no MORE THAN ONE in a row, not "one
        # per candidate run"). A real model may lapse into prose once mid-run
        # and recover; cumulative counting would kill an otherwise productive
        # run on its second lapse.
        if result.tool_uses:
            # CONTINUE with one or more parallel tool calls — all executed in
            # order (e.g. search_wardrobe + inspect_outfit in one model turn).
            return {
                "pending_tools": [
                    {"name": item.name, "arguments": item.arguments}
                    for item in result.tool_uses
                ],
                "trace": trace,
                "trajectory_protocol_errors": 0,
            }

        decision: StylistDecision = result.decision
        if decision.control == "NEED_USER":
            return {
                "handoff_result": AgentHandoffResult(
                    status="NEEDS_CLARIFICATION",
                    clarification=decision.clarification,
                    trace_summary=_trace_summary(trace),
                ),
                "trace": trace,
                "trajectory_done": True,
                "trajectory_protocol_errors": 0,
            }
        # CANDIDATE_READY — the working draft IS the candidate; the parent
        # validates and persists it. We never call a tool on this turn.
        return {
            "handoff_result": AgentHandoffResult(
                status="COMPLETED",
                trace_summary=_trace_summary(trace),
            ),
            "trace": trace,
            "trajectory_done": True,
            "trajectory_protocol_errors": 0,
        }

    def tool_step(state: StylistState) -> dict[str, Any]:
        remaining = list(state.get("pending_tools") or [])
        pending = remaining.pop(0)
        tool_result = runtime.execute_tool(
            pending["name"],
            pending["arguments"],
            state=dict(state),
        )
        updates: dict[str, Any] = {
            "tool_observations": state.get("tool_observations", [])
            + [{"tool": pending["name"], "observation": tool_result.observation}],
            "trajectory_step_count": state.get("trajectory_step_count", 0) + 1,
            "pending_tools": remaining,
        }
        # Only revision rounds (gate_feedback set) consume the bounded-revision
        # budget; a fresh candidate from the empty base draft is unrestricted.
        if state.get("gate_feedback"):
            updates["revision_steps"] = state.get("revision_steps", 0) + 1
        # Stateful tools (modify_outfit → new working_draft) write back through
        # state_updates; the graph node persists them — no second state source.
        updates.update(tool_result.state_updates or {})
        return updates

    def _route(state: StylistState) -> str:
        # Route on the private done flag, never on the shared handoff_result
        # (a stale parent envelope would otherwise end a fresh run early).
        if state.get("trajectory_done"):
            return END
        if state.get("pending_tools"):
            return "tool_step"
        return "stylist_agent"  # protocol-error re-entry with an appended observation

    builder = StateGraph(StylistState)
    builder.add_node("stylist_agent", stylist_agent)
    builder.add_node("tool_step", tool_step)
    builder.add_edge(START, "stylist_agent")
    builder.add_conditional_edges(
        "stylist_agent",
        _route,
        {"tool_step": "tool_step", "stylist_agent": "stylist_agent", END: END},
    )
    builder.add_edge("tool_step", "stylist_agent")
    return builder.compile()


def _trace_summary(trace: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "agent": "stylist",
        "turns": len(trace),
        "decisions": [t.get("decision_summary", "") for t in trace[-3:]],
    }
