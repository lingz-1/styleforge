"""Capability registry: three-layer tool availability separation (frozen #12).

The layer split exists so the ``tools=`` payload stays *stable*: it changes
only when a real capability appears or disappears, never with request/session
state, so the stable-prefix fingerprint stays byte-identical across calls.

    Layer 1  registered_for_agent(agent) — the *stable* tool set an agent may
             see, in deterministic (registration) order. Registration order is
             fixed at harness construction; it never depends on the request.
    Layer 2  runtime_available(agent, runtime_capabilities) — the deployable
             subset of Layer 1, filtered only by provider / key / server
             presence (no Tavily → search_web dropped, no weather provider →
             get_weather dropped, no skills root → load_skill dropped, …).
             This is the only layer allowed to change the ``tools=`` payload.
    Layer 3  check_precondition(name, state) — *temporary* execution-time
             conditions ("no working draft yet"). Runs inside ToolRuntime at
             execution time and answers ``PRECONDITION_FAILED``; it never
             removes the tool from the schema, because the schema describes a
             stable capability, not this moment's state.

Tools are never filtered by semantic keywords ("this request mentions a
musical → force search_web"). Whether to *call* a tool is the Agent's
decision; the registry only decides what capabilities exist to call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from styleforge.llm.client import ToolDefinition

# A precondition: ``(state) -> str | None``. ``None`` means the tool may run;
# a string is the reason the Agent sees as a ``PRECONDITION_FAILED`` fact.
Precondition = Callable[[dict[str, Any]], str | None]

# A handler receives the validated input model plus a ToolContext (H1a-3).
Handler = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ToolCapability:
    """One registered tool, full metadata (registration layer only)."""

    name: str
    description: str
    input_model: type[BaseModel]
    handler: Handler
    # Capability keys this tool needs deployed (e.g. {"web_search"}). Empty
    # means always deployable — never changes with request state.
    requires: frozenset[str] = frozenset()
    # Agents whose catalog includes this tool. Empty means visible to all.
    agents: frozenset[str] = frozenset()
    # Temporary state precondition (Layer 3). None means always runnable.
    precondition: Precondition | None = None

    @property
    def input_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()

    def to_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
        )


class CapabilityRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolCapability] = {}

    # -- registration -----------------------------------------------------

    def register_tool(self, capability: ToolCapability) -> None:
        if not capability.name.strip():
            raise ValueError("tool name must not be empty")
        if capability.name in self._tools:
            raise ValueError(f"tool already registered: {capability.name}")
        self._tools[capability.name] = capability

    def contains(self, name: str) -> bool:
        return name in self._tools

    def capability(self, name: str) -> ToolCapability:
        return self._tools[name]

    def names(self) -> list[str]:
        return list(self._tools)

    # -- Layer 1: stable tool set per agent --------------------------------

    def registered_for_agent(self, agent: str) -> list[ToolDefinition]:
        """Stable catalog an agent may see, in deterministic registration order."""
        return [
            capability.to_definition()
            for capability in self._tools.values()
            if not capability.agents or agent in capability.agents
        ]

    # -- Layer 2: deployable subset per agent + capabilities ----------------

    def runtime_available(
        self,
        agent: str,
        runtime_capabilities: frozenset[str],
    ) -> list[ToolDefinition]:
        """Layer 1 minus capability-gated tools whose dependencies are absent."""
        return [
            capability.to_definition()
            for capability in self._tools.values()
            if (not capability.agents or agent in capability.agents)
            and (not capability.requires or runtime_capabilities.issuperset(capability.requires))
        ]

    # -- Layer 3: temporary state precondition ------------------------------

    def check_precondition(self, name: str, state: dict[str, Any]) -> str | None:
        """``None`` means OK; a string is the ``PRECONDITION_FAILED`` reason."""
        capability = self._tools[name]
        if capability.precondition is None:
            return None
        return capability.precondition(state)
