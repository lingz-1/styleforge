"""ContextAssembler: pull the dynamic context sources into a PromptContext.

Runs inside the AgentRuntime *before every Model Call* (frozen #15) — the Main
Graph's BootstrapContext only initialises thread / user / environment base
facts. Freshly-produced ResearchEvidence, staged candidates and critic
feedback must therefore be re-assembled here on the next agent turn.

The visibility policy decides *what may be seen*; this assembler only reads
those gated sources from the Execution State and carries them over. Anything
an agent may not see is never even read from state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from styleforge.agentic.agentic_contract import ResearchEvidence, TaskState
from styleforge.agentic.context.visibility import AgentContextView, ContextVisibilityPolicy
from styleforge.models.agentic_contract import EnvironmentFacts, PlanState


@dataclass
class PromptContext:
    """The assembled dynamic context for ONE model call (C/D layers).

    ``view`` records which sources were enabled so the PromptAssembler renders
    exactly those sections — never re-deriving, never leaking a hidden source.
    """

    view: AgentContextView | None = None
    user_request: str = ""
    goal: str = ""
    task_state: TaskState | None = None
    plan: PlanState | None = None
    environment_facts: EnvironmentFacts | None = None
    research_evidence: ResearchEvidence | None = None
    # the Research subgraph's private RawEvidenceBuffer — only the Evidence
    # Synthesizer's view enables this (frozen #18/#22).
    raw_evidence: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    working_draft: Any = None
    base_draft: Any = None
    thread_context: dict[str, Any] | None = None
    memories: list[Any] = field(default_factory=list)
    loaded_skills: list[str] = field(default_factory=list)
    # D-layer, subgraph-private: an agent's own recent tool observations and the
    # last gate feedback it must replan on (frozen #9, gate loop).
    tool_observations: list[dict[str, Any]] = field(default_factory=list)
    gate_feedback: str | None = None


class ContextAssembler:
    def __init__(self, visibility: ContextVisibilityPolicy | None = None) -> None:
        self.visibility = visibility or ContextVisibilityPolicy()

    def assemble(self, agent: str, state: dict[str, Any]) -> PromptContext:
        """Read the Execution State through the agent's visibility view."""
        view = self.visibility.view_for(agent)
        context = PromptContext(view=view)
        if view.user_request:
            context.user_request = state.get("request", "")
        if view.user_request or view.goal:
            context.goal = state.get("goal", "")
        if view.task_state:
            context.task_state = state.get("task_state")
        if view.plan:
            context.plan = state.get("plan")
        if view.environment_facts:
            context.environment_facts = state.get("environment_facts")
        if view.research_evidence:
            context.research_evidence = state.get("research_evidence")
        if view.raw_evidence:
            context.raw_evidence = list(state.get("raw_evidence") or [])
        if view.candidates:
            context.candidates = list(state.get("candidates") or [])
        if view.drafts:
            context.working_draft = state.get("working_draft")
            context.base_draft = state.get("base_draft")
        if view.thread_context:
            context.thread_context = state.get("thread_context")
        if view.memories:
            context.memories = list(state.get("recalled_memories") or [])
        if view.trajectory:
            context.tool_observations = list(state.get("tool_observations") or [])
        if view.gate_feedback:
            context.gate_feedback = state.get("gate_feedback")
        context.loaded_skills = list(state.get("loaded_skills") or [])
        return context
