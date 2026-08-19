"""Frozen contracts for the Multi-Agent Harness (frozen architecture H1/H2).

Distinct from ``models/agentic_contract.py`` (the Stage-2 three-way boundary):
this module carries the *orchestration* contracts — what the agents hand back
to the harness, how a subgraph reports to its parent, and how the Coordinator
maintains the task state. The shared Harness Protocol stays small; business
Decision schemas are deliberately per-Agent (frozen #7).

Nothing here is imported by the legacy request path yet.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field, field_validator, model_validator

from styleforge.models.agentic_contract import InteractionContext, PlanState


class _LenientNulls(BaseModel):
    """Base for evidence models: treat explicit ``null`` as field-absent.

    Structured-output providers routinely emit ``"name": null`` for fields the
    model has no value for. pydantic's defaults only cover *missing* fields, so
    an explicit null trips ``str`` validation. Dropping nulls first lets the
    declared defaults (``""``) stand in — the same spirit as
    ``_clarification_before``.
    """

    @model_validator(mode="before")
    @classmethod
    def _drop_none_fields(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: item for key, item in value.items() if item is not None}
        return value


def _clarification_before(value: Any) -> Any:
    """Normalise a no-op clarification to None.

    Tool-calling providers routinely emit ``"clarification": {}`` (or a question
    left empty) when the agent does NOT want to clarify — the model has no "omit"
    syntax for an optional field it includes. Treating that as a real
    ClarificationRequest would fail ``question``-required validation and burn the
    whole protocol-error budget on a field the agent never meant to set.
    """
    if isinstance(value, dict) and not (value.get("question") or "").strip():
        return None
    return value


# ── Clarification (NEED_USER payload, shared across all agents) ────────────

class ClarificationRequest(BaseModel):
    """The payload every ``NEED_USER`` decision must carry (frozen #16)."""

    question: str  # surfaced by the Main Graph's ClarificationNode as {status, question}
    reason: str | None = None


# ── Per-Agent Decision contracts (frozen #7, #16, #14) ─────────────────────
# The model outputs a *text decision block* plus native tool calls; the
# AgentRuntime validates the control/tool combination against these schemas.

class CoordinatorDecision(BaseModel):
    """Coordinator = manager-agent: handoff or plan update, never a tool storm.

    State machine (three mutually-exclusive modes, frozen #16/#14):
      need_user=True        → clarification required, next_agent=None, 0 tools
      need_plan_update=True → next_agent=None, exactly one update_plan call,
                              back to Coordinator, handoff on the NEXT turn
      otherwise             → next_agent required, 0 tools, handoff
    """

    decision_summary: str  # observable summary; Trace records it, never hidden reasoning
    goal: str
    next_agent: Literal["RESEARCH", "STYLIST"] | None = None
    need_plan_update: bool = False
    need_user: bool = False
    clarification: ClarificationRequest | None = None
    _empty_clarification = field_validator("clarification", mode="before")(
        staticmethod(_clarification_before)
    )


class ResearchDecision(BaseModel):
    """Research decides when it has investigated enough (frozen #6/#18)."""

    decision_summary: str
    control: Literal["CONTINUE", "RESEARCH_COMPLETE", "NEED_USER"]
    clarification: ClarificationRequest | None = None  # NEED_USER → required
    _empty_clarification = field_validator("clarification", mode="before")(
        staticmethod(_clarification_before)
    )


class StylistDecision(BaseModel):
    """Stylist decides when a candidate is ready (frozen #8)."""

    decision_summary: str
    control: Literal["CONTINUE", "CANDIDATE_READY", "NEED_USER"]
    clarification: ClarificationRequest | None = None  # NEED_USER → required
    _empty_clarification = field_validator("clarification", mode="before")(
        staticmethod(_clarification_before)
    )


# ── Harness-side state-machine check (frozen #16/#14) ───────────────────────
# The frozen contracts are enforced *here*, not in pydantic field validators,
# so the exact same rules run on every decision, every re-entry, and on
# reconstructed decisions. The AgentRuntime calls this before acting on a
# decision and treats any violation as AGENT_PROTOCOL_ERROR.

def check_decision_contract(decision: BaseModel) -> list[str]:
    """Validate a decision against its Agent's frozen state machine.

    Returns the list of protocol violations (empty = legal). Rules:
      Coordinator: three mutually-exclusive modes — need_user (clarification
                   required, next_agent=None, 0 tools) / need_plan_update
                   (next_agent=None, exactly one update_plan, handoff next
                   turn) / otherwise next_agent required.
      Research/Stylist: NEED_USER requires clarification; clarification is
                   only meaningful with NEED_USER.
    """
    if isinstance(decision, CoordinatorDecision):
        violations: list[str] = []
        if decision.need_user and decision.need_plan_update:
            violations.append("need_user and need_plan_update are mutually exclusive")
        if decision.need_user:
            if decision.clarification is None or not decision.clarification.question.strip():
                violations.append("need_user requires a clarification.question")
            if decision.next_agent is not None:
                violations.append("need_user must leave next_agent unset")
        if decision.need_plan_update and decision.next_agent is not None:
            violations.append("need_plan_update must leave next_agent unset")
        if (
            not decision.need_user
            and not decision.need_plan_update
            and decision.next_agent is None
        ):
            violations.append("a handoff turn requires next_agent")
        if decision.clarification is not None and not decision.need_user:
            violations.append("clarification is only meaningful with need_user")
        return violations
    if isinstance(decision, (ResearchDecision, StylistDecision)):
        if decision.control == "NEED_USER":
            if decision.clarification is None or not decision.clarification.question.strip():
                return ["NEED_USER requires a clarification.question"]
            return []
        if decision.clarification is not None:
            return ["clarification is only meaningful with NEED_USER"]
        return []
    return [f"unknown decision type: {type(decision).__name__}"]


# ── Subgraph → Parent envelope (frozen #20/#21) ─────────────────────────────

class AgentHandoffResult(BaseModel):
    """The only thing a subgraph returns to its parent.

    A subgraph never jumps to a parent-owned node (no ClarificationNode inside
    a subgraph); it RETURNs this envelope and lets the Main Graph route.
    """

    status: Literal["COMPLETED", "NEEDS_CLARIFICATION", "PROTOCOL_ERROR"]
    clarification: ClarificationRequest | None = None
    trace_summary: dict[str, Any] | None = None


# ── Research → Stylist evidence contract (frozen #6) ───────────────────────

class EvidenceSource(_LenientNulls):
    kind: str = ""  # web | weather | knowledge | skill
    title: str = ""
    url: str = ""
    snippet: str = ""


class EventFact(_LenientNulls):
    name: str = ""
    description: str = ""


class VenueFact(_LenientNulls):
    name: str = ""
    location: str = ""
    indoor: bool | None = None


class EventTiming(_LenientNulls):
    date: str = ""
    time: str = ""


class WeatherFact(_LenientNulls):
    location: str = ""
    temperature_c: str = ""
    condition: str = ""


class ResearchEvidence(BaseModel):
    """The formal Research → Stylist handoff (frozen #6).

    ``uncertainties`` is mandatory-spirited: Research must say plainly when it
    found no official dress code / no weather, so Stylist never over-infers.
    """

    event: EventFact | None = None
    venue: VenueFact | None = None
    timing: EventTiming | None = None
    weather: WeatherFact | None = None
    dress_context: list[str] = Field(default_factory=list)
    practical_requirements: list[str] = Field(default_factory=list)
    restrictions: list[str] = Field(default_factory=list)
    theme_elements: list[str] = Field(default_factory=list)
    sources: list[EvidenceSource] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)


class RawEvidence(BaseModel):
    """One raw evidence record held in the Research subgraph's private state."""

    source: EvidenceSource = Field(default_factory=EvidenceSource)
    content: str | dict[str, Any] = ""


class ToolObservation(BaseModel):
    """One raw tool observation held in the Research subgraph's private state."""

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    observation: str | dict[str, Any] = ""


# ── TaskState (Coordinator's long-lived working note, frozen #5) ────────────

class TaskState(BaseModel):
    goal: str = ""
    plan: PlanState | None = None
    completed: list[str] = Field(default_factory=list)
    pending: list[str] = Field(default_factory=list)
    next_agent: str | None = None


# ── Subgraph-private / Main state shapes ────────────────────────────────────

class ResearchState(TypedDict):
    """Research subgraph private state — never enters the Main State (frozen #18)."""

    messages: list[dict[str, Any]]  # agent trajectory (isolated)
    goal: str
    raw_evidence: list[RawEvidence]  # RawEvidenceBuffer: synthesizer input
    tool_observations: list[ToolObservation]
    trace: list[dict[str, Any]]
    step_count: int


class StyleForgeState(TypedDict, total=False):
    """Main Execution State — all serializable; Runtime Dependencies stay out.

    Only cross-agent products live here; each agent's messages live in its
    subgraph private state (frozen #9).
    """

    user_id: str
    thread_id: str
    request: str
    interaction: InteractionContext

    goal: str
    plan: PlanState | None
    task_state: TaskState | None
    current_agent: str
    status: str

    environment_facts: Any  # EnvironmentFacts (runtime-shaped, kept for assembler)
    thread_context: dict[str, Any] | None
    recalled_memories: list[Any]
    loaded_skills: list[str]

    research_evidence: ResearchEvidence | None
    base_draft: Any  # OutfitDraft: recommend=empty base, modify=original snapshot
    working_draft: Any  # current candidate in progress

    candidates: list[Any]  # StageCandidate entries (status=STAGED + run_id)
    pending_tool_calls: list[Any]

    # Subgraph → Parent envelope + Main-Graph gate bookkeeping (H1b).
    run_id: str  # idempotency key for StageCandidate / PersistCandidates
    handoff_result: AgentHandoffResult | None  # frozen #20: subgraph product
    gate_feedback: str | None  # last Env/Critic feedback for the next candidate
    critic_result: Any  # ReviewResult from the Main-Graph Critic node
    environment_valid: bool
    candidate_retries: int  # consecutive Critic rejections for the CURRENT candidate
    # (a bounded replan budget: after MAX_CRITIC_RETRIES rejections the gate
    #  accepts the physically-valid candidate instead of looping forever)
    degraded_accept: bool  # last Critic reject was force-accepted by the budget
    # (stage_candidate marks the entry DEGRADED_ACCEPTED, never a fabricated PASS)
    enough_candidates: bool  # goal_gate: len(candidates) >= target_candidates
    clarification_question: str | None  # ClarificationNode output (frozen #16)

    step_count: int
    llm_call_count: int
    token_usage: int
    protocol_error_count: int  # AGENT_PROTOCOL_ERROR hard cap (frozen #21)

    trace_summaries: list[dict[str, Any]]
