"""StyleForgeHarness: the assembled Multi-Agent Harness.

One object owns the harness wiring:
    CapabilityRegistry(+ 8 local tools) → AgentRuntime (Visibility/Assembler/
    PromptAssembler/Guard/LLM) → compiled Main Graph (Coordinator + Stylist
    chain + Clarification).

Runtime Dependencies (llm, environment, providers) are captured here — never in
the serializable Execution State. ``invoke(state)`` is the single Agent runtime
entry point used by the current ``MultiTaskWorkflow`` request path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from styleforge.agentic.agentic_contract import StyleForgeState
from styleforge.agentic.context.evidence_store import EvidenceStore
from styleforge.agentic.context.guard import ContextGuard
from styleforge.agentic.context.visibility import ContextVisibilityPolicy
from styleforge.agentic.graph.main import build_h2a_main_graph
from styleforge.agentic.hooks.manager import HookManager
from styleforge.agentic.runtime.agent_runtime import AgentRuntime
from styleforge.agentic.runtime.capability_registry import CapabilityRegistry
from styleforge.agentic.tools.local_tools import register_local_tools

DEFAULT_INSTRUCTIONS_ROOT = (
    Path(__file__).resolve().parent / "instructions"
)


class StyleForgeHarness:
    """The compiled Multi-Agent Harness over one LLM + one Environment."""

    def __init__(
        self,
        *,
        llm: Any,
        environment: Any,
        instructions_root: Path | str = DEFAULT_INSTRUCTIONS_ROOT,
        runtime_capabilities: frozenset[str] = frozenset(),
        hooks: HookManager | None = None,
        guard: ContextGuard | None = None,
        visibility: ContextVisibilityPolicy | None = None,
        agent_instruction_versions: dict[str, str] | None = None,
        knowledge_retriever: Any | None = None,
        target_candidates: int = 3,
        # Layered PreferenceRetriever; default None keeps the compact
        # ``recalled_memories`` fallback for direct test/integration callers.
        memory_retriever: Any | None = None,
    ) -> None:
        self.registry = CapabilityRegistry()
        register_local_tools(self.registry, environment, knowledge_retriever=knowledge_retriever)
        self.runtime = AgentRuntime(
            llm=llm,
            registry=self.registry,
            instructions_root=instructions_root,
            runtime_capabilities=runtime_capabilities,
            hooks=hooks,
            visibility=visibility,
            guard=guard,
            agent_instruction_versions=agent_instruction_versions,
            memory_retriever=memory_retriever,
        )
        self.evidence_store = EvidenceStore()
        self.graph = build_h2a_main_graph(
            self.runtime,
            environment=environment,
            target_candidates=target_candidates,
            evidence_store=self.evidence_store,
        )

    @property
    def model_calls(self) -> int:
        """Total LLM calls this harness has spent across all invokes."""
        return self.runtime.model_calls

    @property
    def llm_usage(self) -> dict[str, Any]:
        """Detailed foreground LLM usage for this harness invocation."""
        return self.runtime.llm_usage

    def invoke(self, state: StyleForgeState) -> dict[str, Any]:
        """Run the compiled Main Graph over one execution state."""
        return self.graph.invoke(dict(state))
