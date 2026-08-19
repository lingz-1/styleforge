"""Main Graph: bootstrap → (Coordinator) → Stylist chain + Clarification.

H1b graph (no Coordinator):
    START → bootstrap → Stylist subgraph → route on AgentHandoffResult:
        COMPLETED          → Environment Gate → Critic → PASS → StageCandidate
                             → Goal Gate (enough?) → YES → end(DONE)
                                                    → NO  → ResetCandidateDraft → Stylist
                             Critic FAIL / Env fail → gate_feedback → Stylist (replan)
        NEEDS_CLARIFICATION → ClarificationNode (Main-Graph owned, frozen #20) → end
        PROTOCOL_ERROR     → end(AGENT_PROTOCOL_ERROR, frozen #21)

H2a graph adds the Coordinator in front (frozen #5): it maintains TaskState and
hands off to STYLIST / RESEARCH; the parent routes the COMPLETED envelope on
``task_state.next_agent``. RESEARCH is a placeholder until H2b.

The Main Graph owns the entire verification chain (Environment Gate → Critic →
StageCandidate → enough?); subgraphs only produce.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from styleforge.agentic.agentic_contract import StyleForgeState
from styleforge.agentic.agents.coordinator.graph import build_coordinator_subgraph
from styleforge.agentic.agents.critic.graph import make_critic_node
from styleforge.agentic.agents.research.graph import build_research_subgraph
from styleforge.agentic.agents.stylist.graph import build_stylist_subgraph
from styleforge.agentic.gates.environment import make_environment_gate
from styleforge.agentic.gates.goal import make_goal_gate
from styleforge.agentic.runtime.agent_runtime import AgentRuntime

# Staged candidate entries (frozen #19: STAGED + run_id, never persisted here).
# DEGRADED_ACCEPTED: the bounded-replan budget force-accepted a physically-valid
# candidate the Critic kept rejecting (finite wardrobe can't always yield a
# third distinct direction). Logged honestly — never a fabricated PASS.
_CANDIDATE_STATUS_STAGED = "STAGED"
_CANDIDATE_STATUS_DEGRADED = "DEGRADED_ACCEPTED"

# Bounded replan budget per candidate (frozen #7 diversity). A real provider
# keeps trying to satisfy the Critic; with a finite wardrobe the third
# candidate can drift into an unbounded "similar → rejected → retry" loop.
# After this many consecutive rejections the gate accepts the physically-valid
# candidate (flagged in gate_feedback) instead of looping forever.
MAX_CRITIC_RETRIES = 3


def build_h1b_main_graph(
    runtime: AgentRuntime,
    *,
    environment: Any,
    target_candidates: int = 3,
):
    """H1b graph: Stylist chain + Clarification, no Coordinator."""
    return _build_main_graph(
        runtime,
        environment=environment,
        target_candidates=target_candidates,
        with_coordinator=False,
    )


def build_h2a_main_graph(
    runtime: AgentRuntime,
    *,
    environment: Any,
    target_candidates: int = 3,
    evidence_store: Any | None = None,
):
    """H2a graph: Coordinator handoff → Research / Stylist + Clarification."""
    return _build_main_graph(
        runtime,
        environment=environment,
        target_candidates=target_candidates,
        with_coordinator=True,
        evidence_store=evidence_store,
    )


def _build_main_graph(
    runtime: AgentRuntime,
    *,
    environment: Any,
    target_candidates: int,
    with_coordinator: bool,
    evidence_store: Any | None = None,
):
    """Shared Main-Graph builder. ``with_coordinator`` splices the Coordinator +
    its routes in front of the Stylist chain (H2a+); the chain itself is untouched."""

    stylist_subgraph = build_stylist_subgraph(runtime)
    critic_node = make_critic_node(runtime)
    environment_gate = make_environment_gate(environment)
    goal_gate = make_goal_gate(target_candidates)
    research_subgraph = build_research_subgraph(runtime)

    def bootstrap(state: StyleForgeState) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        if state.get("working_draft") is None and state.get("base_draft") is not None:
            updates["working_draft"] = state["base_draft"]
        # BootstrapContext (frozen #15): the Environment's pre-legacy facts are
        # base facts for every agent view. Without them the Execution State
        # carries no wardrobe summary, so the Stylist's prompt has no item ids
        # to compose from — it falls back to blind search_wardrobe probing and
        # burns the whole step budget chasing a query. The facts travel with
        # the Environment runtime dependency, not the request state.
        if state.get("environment_facts") is None:
            facts = getattr(environment, "facts", None)
            if facts is not None:
                updates["environment_facts"] = facts
        return updates

    def research(state: StyleForgeState) -> dict[str, Any]:
        # H2b: the Research Subgraph produces a ResearchEvidence; the raw buffer
        # stays inside (frozen #18). The harness-side EvidenceStore journals the
        # product for tracing/audit — a Runtime Dependency, never in the state.
        result = research_subgraph.invoke(state)
        if (
            evidence_store is not None
            and result.get("research_evidence") is not None
        ):
            evidence_store.save(result["research_evidence"], state.get("run_id", ""))
        return result

    def critic(state: StyleForgeState) -> dict[str, Any]:
        result = critic_node(state)
        approved = result["critic_result"].approved
        retries = state.get("candidate_retries", 0) + (0 if approved else 1)
        if approved:
            feedback: str | None = None
        else:
            base = result["critic_result"].feedback or "；".join(result["critic_result"].issues)
            if retries >= MAX_CRITIC_RETRIES:
                # Budget exhausted — the next route_after_critic accepts the
                # candidate. Flag the degraded accept so the caller can tell a
                # genuinely-diverse set from a bounded one.
                feedback = f"{base}（已达多样性重试上限，本次接受该候选）"
            else:
                feedback = base
        # degraded_accept: the NEXT stage_candidate marks the entry honestly
        # (DEGRADED_ACCEPTED, not a fabricated PASS) when this accept was forced.
        degraded_accept = not approved and retries >= MAX_CRITIC_RETRIES
        return {
            **result,
            "gate_feedback": feedback,
            "candidate_retries": retries,
            "degraded_accept": degraded_accept,
        }

    def stage_candidate(state: StyleForgeState) -> dict[str, Any]:
        draft = state["working_draft"]
        status = (
            _CANDIDATE_STATUS_DEGRADED
            if state.get("degraded_accept", False)
            else _CANDIDATE_STATUS_STAGED
        )
        entry = {
            "status": status,
            "run_id": state.get("run_id", ""),
            "outfit": draft.outfit,
            "item_ids": list(draft.outfit.item_ids),
        }
        return {"candidates": list(state.get("candidates") or []) + [entry]}

    def reset_candidate_draft(state: StyleForgeState) -> dict[str, Any]:
        # Harness node (frozen #8): reset to the base draft — recommend: empty
        # base; modify: the original snapshot. NEVER to a previous candidate.
        # A fresh candidate also starts a fresh Critic-replan budget.
        return {
            "working_draft": state["base_draft"],
            "gate_feedback": None,
            "candidate_retries": 0,
            "degraded_accept": False,
        }

    def clarification_node(state: StyleForgeState) -> dict[str, Any]:
        handoff = state["handoff_result"]
        question = handoff.clarification.question
        return {"status": "needs_clarification", "clarification_question": question}

    def end_node(state: StyleForgeState) -> dict[str, Any]:
        handoff = state.get("handoff_result")
        if handoff is not None and handoff.status == "PROTOCOL_ERROR":
            return {"status": "agent_protocol_error"}
        if state.get("clarification_question"):
            return {}  # clarification_node already set the status
        if state.get("status"):
            return {}  # an earlier terminal node already set the status
        return {"status": "done" if state.get("enough_candidates") else "ended"}

    # routing -------------------------------------------------------------

    def route_after_stylist(state: StyleForgeState) -> str:
        handoff = state.get("handoff_result")
        if handoff is None:
            return "end_node"
        return {
            "COMPLETED": "environment_gate",
            "NEEDS_CLARIFICATION": "clarification",
            "PROTOCOL_ERROR": "end_node",
        }[handoff.status]

    def route_after_coordinator(state: StyleForgeState) -> str:
        handoff = state.get("handoff_result")
        if handoff is None:
            return "end_node"
        if handoff.status == "NEEDS_CLARIFICATION":
            return "clarification"
        if handoff.status == "PROTOCOL_ERROR":
            return "end_node"
        # COMPLETED — the Coordinator's product is the TaskState (frozen #5).
        task_state = state.get("task_state")
        next_agent = task_state.next_agent if task_state is not None else None
        return {"STYLIST": "stylist", "RESEARCH": "research"}.get(next_agent, "end_node")

    def route_after_research(state: StyleForgeState) -> str:
        handoff = state.get("handoff_result")
        if handoff is None:
            return "end_node"
        return {
            "COMPLETED": "coordinator",  # evidence shared → Coordinator updates TaskState
            "NEEDS_CLARIFICATION": "clarification",
            "PROTOCOL_ERROR": "end_node",
        }[handoff.status]

    def route_after_environment(state: StyleForgeState) -> str:
        return "critic" if state.get("environment_valid") else "stylist"

    def route_after_critic(state: StyleForgeState) -> str:
        if state["critic_result"].approved:
            return "stage_candidate"
        if state.get("candidate_retries", 0) >= MAX_CRITIC_RETRIES:
            # Replan budget exhausted (frozen #7 bounded diversity): accept the
            # physically-valid candidate so a finite wardrobe can still fill the
            # target count. The degraded flag is already in gate_feedback.
            return "stage_candidate"
        return "stylist"

    def route_after_goal(state: StyleForgeState) -> str:
        return "end_node" if state.get("enough_candidates") else "reset_candidate_draft"

    # wiring --------------------------------------------------------------

    builder = StateGraph(StyleForgeState)
    builder.add_node("bootstrap", bootstrap)
    builder.add_node("stylist", stylist_subgraph)
    builder.add_node("environment_gate", environment_gate)
    builder.add_node("critic", critic)
    builder.add_node("stage_candidate", stage_candidate)
    builder.add_node("goal_gate", goal_gate)
    builder.add_node("reset_candidate_draft", reset_candidate_draft)
    builder.add_node("clarification", clarification_node)
    builder.add_node("end_node", end_node)

    if with_coordinator:
        builder.add_node("coordinator", build_coordinator_subgraph(runtime))
        builder.add_node("research", research)

    builder.add_edge(START, "bootstrap")

    if with_coordinator:
        builder.add_edge("bootstrap", "coordinator")
        builder.add_conditional_edges(
            "coordinator",
            route_after_coordinator,
            {
                "stylist": "stylist",
                "research": "research",
                "clarification": "clarification",
                "end_node": "end_node",
            },
        )
        builder.add_conditional_edges(
            "research",
            route_after_research,
            {
                "coordinator": "coordinator",  # evidence back to the manager
                "clarification": "clarification",
                "end_node": "end_node",
            },
        )
    else:
        builder.add_edge("bootstrap", "stylist")

    builder.add_conditional_edges(
        "stylist",
        route_after_stylist,
        {
            "environment_gate": "environment_gate",
            "clarification": "clarification",
            "end_node": "end_node",
        },
    )
    builder.add_conditional_edges(
        "environment_gate",
        route_after_environment,
        {"critic": "critic", "stylist": "stylist"},
    )
    builder.add_conditional_edges(
        "critic",
        route_after_critic,
        {"stage_candidate": "stage_candidate", "stylist": "stylist"},
    )
    builder.add_edge("stage_candidate", "goal_gate")
    builder.add_conditional_edges(
        "goal_gate",
        route_after_goal,
        {"end_node": "end_node", "reset_candidate_draft": "reset_candidate_draft"},
    )
    builder.add_edge("reset_candidate_draft", "stylist")
    builder.add_edge("clarification", "end_node")
    builder.add_edge("end_node", END)
    return builder.compile()
