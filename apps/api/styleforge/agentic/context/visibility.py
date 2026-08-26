"""ContextVisibilityPolicy: which context sources an agent may see (frozen #15).

This is the answer to "what can THIS agent look at" — a pure matrix, no I/O.
The ContextAssembler runs it *before every Model Call* (not at a fixed Main
Graph node), so freshly-produced ResearchEvidence / staged candidates / critic
feedback are re-assembled into the next call.

    Coordinator           request / task_state / plan / evidence summary /
                          candidate status — never raw wardrobe Top-K.
    Research              research goal / plan / prior evidence / thread /
                          memories — never the Stylist trajectory.
    Stylist               request / task_state / plan / environment facts /
                          wardrobe / evidence / drafts / staged candidates /
                          memories — never raw Tavily pages or the
                          Coordinator's internal trajectory.
    Critic                request / evidence / staged candidates / the draft
                          under review — never the Stylist tool trajectory.
    research_synthesizer  research goal + raw evidence only (its subgraph
                          feeds those directly; see H2b).

Agent *messages* are never shared through this policy — each agent's
trajectory lives in its subgraph private state (frozen #9).
"""

from __future__ import annotations

from dataclasses import dataclass

from styleforge.agentic.tools.local_tools import (
    AGENT_COORDINATOR,
    AGENT_CRITIC,
    AGENT_EXTENSION,
    AGENT_RESEARCH,
    AGENT_RESEARCH_SYNTHESIZER,
    AGENT_STYLIST,
)


@dataclass(frozen=True, slots=True)
class AgentContextView:
    """A boolean mask over the dynamic context sources the assembler may read.

    Each flag gates exactly one section the PromptAssembler renders into the
    C/D layers. Everything an agent should not see stays unset — the assembler
    never even reads it from state.
    """

    user_request: bool = False
    goal: bool = False  # split from user_request: a fixed node may need the goal but not the raw request
    task_state: bool = False
    plan: bool = False
    environment_facts: bool = False
    wardrobe: bool = False
    research_evidence: bool = False
    raw_evidence: bool = False  # the Research subgraph's private RawEvidenceBuffer
    extension_facts: bool = False  # execute-side precomputed deterministic facts (Agent1)
    candidates: bool = False
    drafts: bool = False
    thread_context: bool = False
    memories: bool = False
    grounding: bool = False  # H3a: the deterministic grounding context (独立 C 层 section)
    trajectory: bool = False  # an agent's OWN recent tool observations (frozen #9)
    gate_feedback: bool = False  # last Env/Critic feedback for the next candidate

    def enabled(self, source: str) -> bool:
        return bool(getattr(self, source, False))


_VIEWS: dict[str, AgentContextView] = {
    AGENT_COORDINATOR: AgentContextView(
        user_request=True,
        task_state=True,
        plan=True,
        research_evidence=True,
        extension_facts=True,  # presence marks the task as EXTENSION (ext dispatch)
        candidates=True,
        thread_context=True,
        grounding=True,  # H3a: today's date / current city / decision
    ),
    AGENT_RESEARCH: AgentContextView(
        user_request=True,
        plan=True,
        research_evidence=True,
        thread_context=True,
        memories=True,
        grounding=True,  # H3a: search-before-ask — the research agent needs it
        trajectory=True,  # its own tool observations while researching
    ),
    AGENT_STYLIST: AgentContextView(
        user_request=True,
        task_state=True,
        plan=True,
        environment_facts=True,
        wardrobe=True,
        research_evidence=True,
        candidates=True,
        drafts=True,
        thread_context=True,
        memories=True,
        grounding=True,  # H3a: date/city/season basis for outfit inference
        trajectory=True,  # its own ReAct loop observations
        gate_feedback=True,  # last Env/Critic feedback to replan on
    ),
    AGENT_CRITIC: AgentContextView(
        user_request=True,
        research_evidence=True,
        candidates=True,
        drafts=True,
    ),
    # Evidence Synthesizer: a fixed node (frozen #22), fed only its subgraph's
    # research goal + raw evidence buffer + tool observations. It never sees the
    # wardrobe, the Stylist trajectory or unrelated memory — and it has no tools
    # (frozen #6/#18: it only reorganizes what already exists).
    AGENT_RESEARCH_SYNTHESIZER: AgentContextView(
        goal=True,
        raw_evidence=True,
        trajectory=True,  # the Research subgraph's own tool observations
    ),
    # Extension: the deterministic facts layer is its anchor input (analyze
    # results for the task_type), plus the wardrobe/knowledge/search tooling and
    # layered preferences. It never sees drafts/staged candidates — no outfit
    # product, no verification chain.
    AGENT_EXTENSION: AgentContextView(
        user_request=True,
        task_state=True,
        plan=True,
        environment_facts=True,
        wardrobe=True,
        extension_facts=True,
        thread_context=True,
        memories=True,
        grounding=True,  # H3a: date/city/season basis for style analysis
        trajectory=True,  # its own ReAct loop observations
    ),
}


class ContextVisibilityPolicy:
    """Stateless policy: ``view_for(agent)`` returns the frozen view matrix."""

    def view_for(self, agent: str) -> AgentContextView:
        try:
            return _VIEWS[agent]
        except KeyError as error:
            raise ValueError(f"no context view for agent: {agent}") from error
