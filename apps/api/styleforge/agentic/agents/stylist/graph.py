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
from styleforge.core.categories import infer_slot

# Step hard cap (safety, mirrors the legacy LoopConfig.max_steps). On exceed the
# subgraph returns PROTOCOL_ERROR as the harness-internal "did not produce" stop;
# it is NOT a fabricated candidate. 12 rather than 8: a real provider exploring
# a fresh candidate after a Critic rejection burns several tool turns before
# converging (search + build + adjust). Eight tool turns still allow three slot
# searches, one build and bounded corrections, while preventing one candidate
# from monopolising a user request for minutes.
MAX_STYLIST_STEPS = 8

# Bounded revision (frozen #7 real-model deadlock): after a Critic / Env-Gate
# rejection (gate_feedback set) the Stylist may keep editing for at most this
# many tool turns, then MUST re-submit whatever legal draft it has. A real
# provider loops "modify → modify → ..." forever when the wardrobe cannot yield
# a direction distinct enough to satisfy the Critic; forcing the re-submit hands
# control back to the Main Graph where the bounded critic budget
# (MAX_CRITIC_RETRIES) accepts the candidate instead of burning the step cap.
# Only revision rounds are bounded — a fresh candidate from the empty base draft
# is unrestricted (MAX_STYLIST_STEPS still guards it).
MAX_REVISION_STEPS = 2

# Fresh-research cap (real-model "piacon" failure): a FRESH candidate (no
# gate_feedback) may keep re-searching the same facts (search_web / get_weather /
# search_wardrobe) forever without ever calling modify_outfit, burning the step
# cap and ending in PROTOCOL_ERROR with an empty draft — zero candidates from a
# productive-looking run. Once a fresh candidate reaches this many tool turns
# without having built anything, the subgraph injects a one-shot "__protocol__"
# nudge (no model call, no budget) forcing it to compose a complete outfit and
# submit. The step-cap force-submit (below) is the backstop if the nudge is
# ignored. Revision rounds do NOT get the nudge — they already hit the bounded
# revision force-submit at MAX_REVISION_STEPS.
FRESH_RESEARCH_CAP = 3


class StylistState(TypedDict, total=False):
    """Subgraph state. Shared channels flow from/to the Main Graph; private
    trajectory channels never cross back."""

    # context sources (input from the Main Graph, read-only inside the subgraph)
    request: str
    task_type: str
    goal: str
    plan: Any
    task_state: Any
    environment_facts: Any
    user_intent: Any
    auto_submit_after_modify: bool
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
    stylist_has_built: bool  # True once a modify_outfit yields a non-empty draft
    fresh_research_nudged: bool  # one-shot fresh-research nudge already injected
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

    def _draft_has_items(state: StylistState) -> bool:
        working_draft = state.get("working_draft")
        if working_draft is None:
            return False
        outfit = getattr(working_draft, "outfit", None)
        if outfit is None:
            return False
        return bool(getattr(outfit, "item_ids", None))

    def _draft_is_complete_outfit(draft: Any) -> bool:
        outfit = getattr(draft, "outfit", None)
        items = getattr(outfit, "items", None) or []
        slots = {infer_slot(str(getattr(item, "item_type", ""))) for item in items}
        return "footwear" in slots and (
            "one_piece" in slots or {"top", "bottom"} <= slots
        )

    def stylist_agent(state: StylistState) -> dict[str, Any]:
        # Step-cap force-submit (real-model "piacon" failure): a fresh candidate
        # that kept re-searching without building hits MAX_STYLIST_STEPS with a
        # (possibly partial) draft in hand. Submitting whatever legal draft
        # exists beats PROTOCOL_ERROR-with-empty-hands — the Main Graph's
        # Environment Gate / Critic will validate it and, if invalid, hand back
        # feedback into a bounded revision round. Only a genuinely empty draft
        # (nothing ever built) is a PROTOCOL_ERROR.
        if state.get("trajectory_step_count", 0) >= MAX_STYLIST_STEPS:
            if _draft_has_items(state):
                trace = state.get("trace", []) + [
                    {
                        "agent": "stylist",
                        "decision_summary": "已达步数上限，强制提交当前方案",
                        "control": "CANDIDATE_READY",
                    }
                ]
                return _envelope("COMPLETED", trace)
            return _envelope("PROTOCOL_ERROR", state.get("trace", []))
        # Frozen #21 hard cap: re-entry after a protocol error is bounded. A
        # A valid turn below resets the counter, so this bounds CONSECUTIVE
        # failures while preserving recovery from an occasional malformed
        # DeepSeek response. The overall step/revision caps still prevent an
        # otherwise productive candidate from looping forever.
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
            and _draft_has_items(state)
        ):
            trace = state.get("trace", []) + [
                {
                    "agent": "stylist",
                    "decision_summary": "已达修订步数上限，强制提交当前方案",
                    "control": "CANDIDATE_READY",
                }
            ]
            return _envelope("COMPLETED", trace)
        # Fresh-research nudge: a fresh candidate that keeps re-searching without
        # ever building burns the step cap on searches alone. Inject a one-shot
        # protocol observation (no model call — re-enter below) telling it to
        # compose a complete outfit from what it already has and submit. The
        # next call to this node runs the model WITH the nudge in context.
        if (
            not state.get("gate_feedback")
            and not state.get("stylist_has_built")
            and not state.get("fresh_research_nudged")
            and state.get("trajectory_step_count", 0) >= FRESH_RESEARCH_CAP
        ):
            nudge = (
                f"你已连续 {state.get('trajectory_step_count', 0)} 轮只查证（搜索/天气/衣橱）"
                "而未组合任何搭配。基于已有的衣橱搜索结果与【研究证据】，"
                "立即用 modify_outfit 组合一套完整搭配（二选一：一次 add 连衣裙/连体装"
                "+鞋履，或上装+下装+鞋履；再按用户要求添加外套/配饰），随后 "
                "CANDIDATE_READY 提交。不要继续寻找衣橱能力索引显示为 0 的槽位，"
                "也不要重复搜索或查证同一事实。"
            )
            return {
                "fresh_research_nudged": True,
                "tool_observations": state.get("tool_observations", [])
                + [{"tool": "__protocol__", "observation": nudge}],
            }
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
                + [
                    {
                        "tool": "__protocol__",
                        "observation": result.protocol_error,
                        "error_code": result.error_code,
                        "retryable": result.retryable,
                    }
                ],
            }

        trace = state.get("trace", []) + [result.trace]
        decision: StylistDecision = result.decision
        intent_update = (
            {
                "user_intent": decision.intent.model_copy(
                    update={"message": str(state.get("request") or decision.intent.message)}
                )
            }
            if decision.intent is not None
            else {}
        )
        # A valid turn resets the protocol-error budget (frozen #21: re-entry
        # after a protocol error ≤ 1 — i.e. no MORE THAN ONE in a row, not "one
        # per candidate run"). A real model may lapse into prose once mid-run
        # and recover; cumulative counting would kill an otherwise productive
        # run on its second lapse.
        if result.tool_uses:
            # CONTINUE with one or more parallel tool calls — all executed in
            # order (e.g. search_wardrobe + inspect_outfit in one model turn).
            return {
                **intent_update,
                "pending_tools": [
                    {"name": item.name, "arguments": item.arguments} for item in result.tool_uses
                ],
                "trace": trace,
                "trajectory_protocol_errors": 0,
            }

        if decision.control == "NEED_USER":
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
        # CANDIDATE_READY — the working draft IS the candidate; the parent
        # validates and persists it. We never call a tool on this turn.
        return {
            **intent_update,
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
            + [
                {
                    "tool": pending["name"],
                    "observation": tool_result.observation,
                    "error_code": tool_result.error_code,
                    "retryable": tool_result.retryable,
                }
            ],
            "trajectory_step_count": state.get("trajectory_step_count", 0) + 1,
            "pending_tools": remaining,
        }
        # Only revision rounds (gate_feedback set) consume the bounded-revision
        # budget; a fresh candidate from the empty base draft is unrestricted.
        if state.get("gate_feedback"):
            updates["revision_steps"] = state.get("revision_steps", 0) + 1
        # Stateful tools (modify_outfit → new working_draft) write back through
        # state_updates; the graph node persists them — no second state source.
        state_updates = tool_result.state_updates or {}
        updates.update(state_updates)
        # Track "has built": a modify_outfit that lands a non-empty outfit counts
        # as a real build, so the fresh-research nudge won't re-fire on a
        # candidate that has already started composing.
        if pending["name"] == "modify_outfit":
            new_draft = state_updates.get("working_draft")
            new_outfit = getattr(new_draft, "outfit", None) if new_draft is not None else None
            if new_outfit is not None and getattr(new_outfit, "item_ids", None):
                updates["stylist_has_built"] = True
                # A normal modification is complete as soon as the Stylist's
                # semantic intent and mutation have both been accepted by the
                # tool. Enter the Environment Gate and Critic immediately;
                # their rejection still starts the existing bounded replan
                # loop, so this removes only the redundant "ready" model turn.
                can_auto_submit = (
                    state.get("task_type") != "outfit_recommend"
                    or _draft_is_complete_outfit(new_draft)
                )
                if (
                    state.get("auto_submit_after_modify")
                    and not remaining
                    and can_auto_submit
                ):
                    trace = state.get("trace", [])
                    updates.update(_envelope("COMPLETED", trace))
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
    builder.add_conditional_edges(
        "tool_step",
        _route,
        {"tool_step": "tool_step", "stylist_agent": "stylist_agent", END: END},
    )
    return builder.compile()


def _trace_summary(trace: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "agent": "stylist",
        "turns": len(trace),
        "decisions": [t.get("decision_summary", "") for t in trace[-3:]],
    }
