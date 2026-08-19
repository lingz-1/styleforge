"""Research subgraph: gather external facts, then stop (frozen #6/#18).

Owns the Research working trajectory privately. It loops ``research_agent ↔
tool_step`` until the Agent signals RESEARCH_COMPLETE (or NEED_USER), then a
fixed Evidence Synthesizer node turns the private RawEvidenceBuffer into one
structured ``ResearchEvidence`` (frozen #6 — uncertainties mandatory-spirited),
which crosses to the parent as the only product. The raw buffer, tool
observations and trace stay inside (frozen #18): the parent receives
``research_evidence`` + the envelope, nothing else.

Shared channels (goal / plan / thread_context / …) pass across the boundary;
the private ``trajectory_*`` names are namespaced so they can never collide
with the parent's counters (same bug class as the Stylist, frozen #9/#21).
The Synthesizer is a *node inside this subgraph*, not a Main-Graph node — it
has no tools and never continues research (frozen #22).
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from styleforge.agentic.agentic_contract import (
    AgentHandoffResult,
    ResearchDecision,
    ResearchEvidence,
)
from styleforge.agentic.agents.research.synthesize import make_evidence_synthesizer
from styleforge.agentic.runtime.agent_runtime import AgentRuntime, ContextLimitError

# Research is bounded: at most a few web/weather/knowledge/skill calls per goal.
MAX_RESEARCH_STEPS = 6

# tool name → EvidenceSource.kind (frozen #6 source taxonomy).
_TOOL_KIND = {
    "search_web": "web",
    "get_weather": "weather",
    "search_knowledge": "knowledge",
    "load_skill": "skill",
}


class ResearchState(TypedDict, total=False):
    """Subgraph state. Shared channels flow to/from the Main Graph; the private
    trajectory (raw_evidence / tool_observations / trace / counters / flags)
    stays inside and is dropped on RETURN."""

    # context sources (input from the Main Graph, read-only inside the subgraph)
    request: str
    goal: str
    plan: Any
    thread_context: dict | None
    recalled_memories: list[Any]
    loaded_skills: list[str]
    interaction: Any
    # private trajectory — never returned to the parent (frozen #18)
    raw_evidence: list[dict[str, Any]]  # RawEvidenceBuffer, synthesizer input
    tool_observations: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    trajectory_step_count: int
    trajectory_protocol_errors: int
    pending_tools: list[dict[str, Any]]
    trajectory_done: bool  # finished this run — private, never the shared envelope
    trajectory_synthesize: bool  # RESEARCH_COMPLETE → run the closing node
    # products shared with the parent (frozen #6/#20)
    handoff_result: AgentHandoffResult | None
    research_evidence: ResearchEvidence | None


def build_research_subgraph(runtime: AgentRuntime):
    """Compile the Research subgraph over one AgentRuntime."""

    synthesize_node = make_evidence_synthesizer(runtime)

    def _envelope(status: str, trace: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
        return {
            "handoff_result": AgentHandoffResult(
                status=status,
                trace_summary=_trace_summary(trace),
                **extra,
            ),
            "trajectory_done": True,
        }

    def research_agent(state: ResearchState) -> dict[str, Any]:
        if state.get("trajectory_step_count", 0) >= MAX_RESEARCH_STEPS:
            # Step budget exhausted without the Agent signalling
            # RESEARCH_COMPLETE. Research's product is the evidence already
            # gathered, so a forced closing pass is a legitimate wrap-up — the
            # Synthesizer turns the buffer (including uncertainties) into
            # ResearchEvidence. Never a fabricated decision, never an error.
            return {"trajectory_synthesize": True, "trace": state.get("trace", [])}
        # Frozen #21 hard cap: re-entry after a protocol error is bounded. A
        # real provider can get stuck in a prose streak for a few turns; a valid
        # turn (below) resets the counter, so the budget bounds CONSECUTIVE
        # failures (≤ 5 in a row) — enough for DeepSeek's occasional narration
        # mode to break, never an infinite loop.
        if state.get("trajectory_protocol_errors", 0) > 5:
            return _envelope("PROTOCOL_ERROR", state.get("trace", []))
        try:
            result = runtime.call(
                "research",
                dict(state),
                decision_model=ResearchDecision,
            )
        except ContextLimitError:
            return _envelope("PROTOCOL_ERROR", state.get("trace", []))

        if result.protocol_error is not None:
            return {
                "trajectory_protocol_errors": state.get("trajectory_protocol_errors", 0) + 1,
                "tool_observations": state.get("tool_observations", [])
                + [{"tool": "__protocol__", "observation": result.protocol_error}],
            }

        trace = state.get("trace", []) + [result.trace]
        # A valid turn resets the protocol-error budget (same reasoning as the
        # Stylist: frozen #21 bounds CONSECUTIVE re-entries, not the lifetime
        # total — a real model can lapse once and still produce a productive run).
        if result.tool_uses:
            # CONTINUE with one or more parallel tool calls — all executed in
            # order, every observation into the private buffer.
            return {
                "pending_tools": [
                    {"name": item.name, "arguments": item.arguments}
                    for item in result.tool_uses
                ],
                "trace": trace,
                "trajectory_protocol_errors": 0,
            }

        decision: ResearchDecision = result.decision
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
        # RESEARCH_COMPLETE — stop researching; the closing node reorganizes
        # whatever the buffer holds into a structured ResearchEvidence.
        return {
            "trace": trace,
            "trajectory_synthesize": True,
            "trajectory_protocol_errors": 0,
        }

    def tool_step(state: ResearchState) -> dict[str, Any]:
        remaining = list(state.get("pending_tools") or [])
        pending = remaining.pop(0)
        tool_result = runtime.execute_tool(
            pending["name"],
            pending["arguments"],
            state=dict(state),
        )
        observation = tool_result.observation
        updates: dict[str, Any] = {
            "tool_observations": state.get("tool_observations", [])
            + [{"tool": pending["name"], "observation": observation}],
            "trajectory_step_count": state.get("trajectory_step_count", 0) + 1,
            "pending_tools": remaining,
        }
        # RawEvidenceBuffer (frozen #18): every tool finding lands here for the
        # Synthesizer — carrying its source kind for provenance.
        kind = _TOOL_KIND.get(pending["name"], "")
        if kind:
            updates["raw_evidence"] = state.get("raw_evidence", []) + [
                {
                    "source": {"kind": kind, "title": "", "url": "", "snippet": ""},
                    "content": observation,
                }
            ]
        updates.update(tool_result.state_updates or {})
        return updates

    def synthesize(state: ResearchState) -> dict[str, Any]:
        product = synthesize_node(state)
        return {
            **product,
            "handoff_result": AgentHandoffResult(
                status="COMPLETED",
                trace_summary=_trace_summary(state.get("trace", [])),
            ),
            "trajectory_done": True,
        }

    def _route(state: ResearchState) -> str:
        if state.get("trajectory_done"):
            return END
        if state.get("trajectory_synthesize"):
            return "synthesize"
        if state.get("pending_tools"):
            return "tool_step"
        return "research_agent"  # protocol-error re-entry with an appended observation

    builder = StateGraph(ResearchState)
    builder.add_node("research_agent", research_agent)
    builder.add_node("tool_step", tool_step)
    builder.add_node("synthesize", synthesize)
    builder.add_edge(START, "research_agent")
    builder.add_conditional_edges(
        "research_agent",
        _route,
        {
            "tool_step": "tool_step",
            "research_agent": "research_agent",
            "synthesize": "synthesize",
            END: END,
        },
    )
    builder.add_edge("tool_step", "research_agent")
    builder.add_edge("synthesize", END)
    return builder.compile()


def _trace_summary(trace: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "agent": "research",
        "turns": len(trace),
        "decisions": [t.get("decision_summary", "") for t in trace[-3:]],
    }
