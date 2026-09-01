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
from styleforge.agentic.context.grounding import pending_field_for_question
from styleforge.agentic.agents.extension.graph import (
    build_extension_subgraph,
    can_direct_close_extension,
)
from styleforge.agentic.agents.research.graph import build_research_subgraph
from styleforge.agentic.agents.stylist.graph import build_stylist_subgraph
from styleforge.agentic.gates.environment import make_environment_gate
from styleforge.agentic.intent_constraints import desired_features_for_request
from styleforge.agentic.gates.goal import make_goal_gate
from styleforge.agentic.runtime.agent_runtime import AgentRuntime
from styleforge.core.categories import infer_slot
from styleforge.models.agentic_contract import ModifyOp, ModifyPlan

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
MAX_CRITIC_RETRIES = 2


def _desired_features_for_request(request: str) -> set[str]:
    """Translate explicit user intent into deterministic recovery features."""
    return desired_features_for_request(request)


def _request_needs_outerwear(request: str, desired_features: set[str]) -> bool:
    text = (request or "").lower()
    explicit = ("外套", "大衣", "风衣", "雨衣", "coat", "jacket", "outerwear")
    weather_protection = bool({"waterproof", "warm", "windproof"}.intersection(desired_features))
    return any(marker in text for marker in explicit) or weather_protection


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
    extension_subgraph = build_extension_subgraph(runtime)

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
        if evidence_store is not None and result.get("research_evidence") is not None:
            evidence_store.save(result["research_evidence"], state.get("run_id", ""))
        return result

    def extension(state: StyleForgeState) -> dict[str, Any]:
        # Extension task: the subgraph ships ``extension_result`` (the task
        # contract) + the envelope; the Main Graph routes the envelope only —
        # no outfit chain, no gates, straight to end_node on COMPLETED.
        return extension_subgraph.invoke(state)

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
            if state.get("degraded_accept", False) or state.get("candidate_recovered", False)
            else _CANDIDATE_STATUS_STAGED
        )
        entry = {
            "status": status,
            "run_id": state.get("run_id", ""),
            "outfit": draft.outfit,
            "item_ids": list(draft.outfit.item_ids),
            "environment_valid": bool(state.get("environment_valid", False)),
            "review": state["critic_result"].model_dump(mode="json"),
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
            "candidate_recovered": False,
            "candidate_recovery_attempted": False,
            "candidate_recovery_issues": [],
            "intent_constraint_failed": False,
            "intent_constraint_issues": [],
        }

    def recover_grounded_candidate(state: StyleForgeState) -> dict[str, Any]:
        """Build one wardrobe-grounded candidate after an empty protocol failure."""
        wardrobe_items = list(getattr(environment, "wardrobe_items", None) or [])
        base_draft = state.get("base_draft")
        if not wardrobe_items or base_draft is None:
            return {
                "candidate_recovered": False,
                "candidate_recovery_attempted": True,
                "candidate_recovery_issues": ["wardrobe_or_base_draft_missing"],
            }

        request = str(state.get("request") or "").lower()
        desired_features = _desired_features_for_request(request)

        by_slot: dict[str, list[Any]] = {}
        for item in wardrobe_items:
            by_slot.setdefault(infer_slot(item.item_type), []).append(item)

        def best(slot: str) -> Any | None:
            candidates = by_slot.get(slot) or []
            if not candidates:
                return None
            return max(
                candidates,
                key=lambda item: (
                    len(
                        desired_features.intersection(
                            str(feature).lower() for feature in item.features
                        )
                    ),
                    sum(
                        token in f"{item.name} {item.description}".lower()
                        for token in desired_features
                    ),
                    item.item_id,
                ),
            )

        one_piece = best("one_piece")
        footwear = best("footwear")
        if one_piece is not None and footwear is not None:
            selected = [one_piece, footwear]
        else:
            selected = [best("top"), best("bottom"), footwear]
            if any(item is None for item in selected):
                return {
                    "candidate_recovered": False,
                    "candidate_recovery_attempted": True,
                    "candidate_recovery_issues": ["complete_core_slots_unavailable"],
                }

        if any(marker in request for marker in ("配饰", "首饰", "accessory", "jewelry")):
            accessory = best("accessory")
            if accessory is not None:
                selected.append(accessory)
        if any(marker in request for marker in ("包", "bag")):
            bag = best("bag")
            if bag is not None:
                selected.append(bag)
        if _request_needs_outerwear(request, desired_features):
            outerwear = best("outerwear")
            if outerwear is not None:
                selected.append(outerwear)

        plan = ModifyPlan(
            ops=[ModifyOp(action="add", item_id=item.item_id) for item in selected],
            reasoning=f"基于当前衣橱按用户请求“{state.get('request', '')}”组合完整搭配",
        )
        recovered, issues = environment.modify_outfit(base_draft, plan)
        if recovered is None:
            return {
                "candidate_recovered": False,
                "candidate_recovery_attempted": True,
                "candidate_recovery_issues": list(issues),
                "gate_feedback": "确定性候选恢复失败：" + "；".join(issues),
            }
        return {
            "working_draft": recovered,
            "handoff_result": None,
            "gate_feedback": "Stylist 协议预算耗尽；已从当前衣橱恢复可验证候选",
            "candidate_recovered": True,
            "candidate_recovery_attempted": True,
            "candidate_recovery_issues": [],
        }

    def clarification_node(state: StyleForgeState) -> dict[str, Any]:
        handoff = state["handoff_result"]
        question = handoff.clarification.question
        updates: dict[str, Any] = {
            "status": "needs_clarification",
            "clarification_question": question,
        }
        # H3a-3 pending_field (Question → Answer → Grounding continuity): a
        # city/date question deterministically names the ThreadGrounding field
        # the next bare reply answers — so round-2 "上海" (no 去X看 structure) is
        # still read as the destination. The field rides out via thread_context.
        field = pending_field_for_question(question)
        if field is not None:
            thread = dict(state.get("thread_context") or {})
            grounding = dict(thread.get("thread_grounding") or {})
            grounding["pending_field"] = field
            thread["thread_grounding"] = grounding
            updates["thread_context"] = thread
        return updates

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
        if (
            handoff.status == "PROTOCOL_ERROR"
            and state.get("task_type") == "outfit_recommend"
            and not state.get("candidates")
        ):
            return "recover_candidate"
        return {
            "COMPLETED": "environment_gate",
            "NEEDS_CLARIFICATION": "clarification",
            "PROTOCOL_ERROR": "end_node",
        }[handoff.status]

    def route_after_recovery(state: StyleForgeState) -> str:
        return "environment_gate" if state.get("candidate_recovered") else "end_node"

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
        authoritative_type = str(state.get("task_type") or "")
        if authoritative_type == "outfit_recommend" and next_agent == "EXTENSION":
            next_agent = "STYLIST"
        elif authoritative_type == "outfit_modify":
            next_agent = "STYLIST"
        elif authoritative_type in {
            "style_advice",
            "item_advice",
            "wardrobe_compatibility",
            "wardrobe_gap",
        }:
            next_agent = "EXTENSION"
        return {"STYLIST": "stylist", "RESEARCH": "research", "EXTENSION": "extension"}.get(
            next_agent, "end_node"
        )

    def route_after_extension(state: StyleForgeState) -> str:
        handoff = state.get("handoff_result")
        if handoff is None:
            return "end_node"
        return {
            "COMPLETED": "end_node",  # extension_result already carries the status
            "NEEDS_CLARIFICATION": "clarification",
            "PROTOCOL_ERROR": "end_node",
        }[handoff.status]

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
        if state.get("environment_valid"):
            return "critic"
        if state.get("task_type") == "outfit_recommend" and state.get("intent_constraint_failed"):
            if not state.get("candidate_recovery_attempted"):
                return "recover_candidate"
            return "end_node"
        return "stylist"

    def route_after_critic(state: StyleForgeState) -> str:
        if state["critic_result"].approved:
            return "stage_candidate"
        scores = state["critic_result"].dimension_scores
        severe_intent_mismatch = scores is not None and (
            scores.request_relevance <= 3 or scores.request_specificity <= 3
        )
        if (
            state.get("task_type") == "outfit_recommend"
            and not state.get("candidate_recovered")
            and severe_intent_mismatch
        ):
            return "recover_candidate"
        if state.get("candidate_retries", 0) >= MAX_CRITIC_RETRIES:
            # Replan budget exhausted (frozen #7 bounded diversity): accept the
            # physically-valid candidate so a finite wardrobe can still fill the
            # target count. The degraded flag is already in gate_feedback.
            return "stage_candidate"
        return "stylist"

    def route_after_goal(state: StyleForgeState) -> str:
        return "end_node" if state.get("enough_candidates") else "reset_candidate_draft"

    def route_after_bootstrap(state: StyleForgeState) -> str:
        # Complete, deterministically verified extension facts need no manager
        # planning. Routing them straight to Extension prevents a model
        # coordinator from re-requesting details already resolved from the
        # wardrobe and knowledge base.
        if can_direct_close_extension(dict(state)):
            return "extension"
        task_type = str(state.get("task_type") or "")
        if task_type == "outfit_modify":
            return "stylist"
        if task_type == "outfit_recommend":
            grounding = state.get("grounding_context")
            grounding = grounding if isinstance(grounding, dict) else {}
            needs_research = grounding.get("decision") == "search_first" or (
                bool(grounding.get("destination_city")) and bool(grounding.get("activity"))
            )
            if not needs_research:
                return "stylist"
        return "coordinator"

    # wiring --------------------------------------------------------------

    builder = StateGraph(StyleForgeState)
    builder.add_node("bootstrap", bootstrap)
    builder.add_node("stylist", stylist_subgraph)
    builder.add_node("environment_gate", environment_gate)
    builder.add_node("critic", critic)
    builder.add_node("stage_candidate", stage_candidate)
    builder.add_node("goal_gate", goal_gate)
    builder.add_node("reset_candidate_draft", reset_candidate_draft)
    builder.add_node("recover_candidate", recover_grounded_candidate)
    builder.add_node("clarification", clarification_node)
    builder.add_node("end_node", end_node)

    if with_coordinator:
        builder.add_node("coordinator", build_coordinator_subgraph(runtime))
        builder.add_node("research", research)
        builder.add_node("extension", extension)

    builder.add_edge(START, "bootstrap")

    if with_coordinator:
        builder.add_conditional_edges(
            "bootstrap",
            route_after_bootstrap,
            {
                "coordinator": "coordinator",
                "extension": "extension",
                "stylist": "stylist",
            },
        )
        builder.add_conditional_edges(
            "coordinator",
            route_after_coordinator,
            {
                "stylist": "stylist",
                "research": "research",
                "extension": "extension",
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
        builder.add_conditional_edges(
            "extension",
            route_after_extension,
            {
                "end_node": "end_node",
                "clarification": "clarification",
            },
        )
    else:
        builder.add_edge("bootstrap", "stylist")

    builder.add_conditional_edges(
        "stylist",
        route_after_stylist,
        {
            "environment_gate": "environment_gate",
            "recover_candidate": "recover_candidate",
            "clarification": "clarification",
            "end_node": "end_node",
        },
    )
    builder.add_conditional_edges(
        "recover_candidate",
        route_after_recovery,
        {"environment_gate": "environment_gate", "end_node": "end_node"},
    )
    builder.add_conditional_edges(
        "environment_gate",
        route_after_environment,
        {
            "critic": "critic",
            "stylist": "stylist",
            "recover_candidate": "recover_candidate",
            "end_node": "end_node",
        },
    )
    builder.add_conditional_edges(
        "critic",
        route_after_critic,
        {
            "stage_candidate": "stage_candidate",
            "stylist": "stylist",
            "recover_candidate": "recover_candidate",
        },
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
