"""Evidence Synthesizer: a fixed closing node, NOT a researching agent.

The Research Agent decides *when it has researched enough*; this node only
reorganizes what already exists (frozen #6/#22). Its prompt profile is its own
(``instructions/research_synthesizer.md`` + the ``research_synthesizer``
visibility view) so it can never be confused with the Researcher: no tools,
no more research, no invention — missing facts go into ``uncertainties``.

It is a *fixed node inside the Research subgraph*: the subgraph reaches
RESEARCH_COMPLETE, then runs this one ``chat_json`` call and returns the
structured ``ResearchEvidence`` to the parent. Like the Critic, it takes its
bundle from the shared PromptAssembler (frozen #17) — no hand-built prompt.
"""

from __future__ import annotations

from typing import Any

from styleforge.agentic.agentic_contract import ResearchEvidence
from styleforge.agentic.runtime.agent_runtime import AgentRuntime, ContextLimitError
from styleforge.agentic.tools.local_tools import AGENT_RESEARCH_SYNTHESIZER

# pydantic's own JSON Schema for the contract — the model is the single source
# of truth for the output shape (no hand-maintained duplicate).
_SYNTH_SCHEMA: dict[str, Any] = ResearchEvidence.model_json_schema()


def make_evidence_synthesizer(runtime: AgentRuntime):
    """The Research subgraph's closing node over one AgentRuntime."""

    def synthesize_node(state: dict[str, Any]) -> dict[str, Any]:
        bundle = runtime.assemble_bundle(AGENT_RESEARCH_SYNTHESIZER, dict(state), tools=[])
        guard = runtime.guard.check(bundle)
        if guard.bundle is None:
            raise ContextLimitError(
                f"context guard: {guard.status}: {'；'.join(guard.warnings)}"
            )
        with runtime.llm_scope(AGENT_RESEARCH_SYNTHESIZER):
            payload, _ = runtime.llm.chat_json(
                system=guard.bundle.system_text,
                user=guard.bundle.model_user_message,
                json_schema=_SYNTH_SCHEMA,
            )
        return {"research_evidence": ResearchEvidence.model_validate(payload)}

    return synthesize_node
