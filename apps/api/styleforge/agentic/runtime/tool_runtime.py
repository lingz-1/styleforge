"""ToolRuntime: execute a tool call through the harness gate chain (frozen §7).

Chain: schema validate → PreToolUse hook → precondition (Layer 3) → handler →
PostToolUse / OnToolError → normalized ToolCallResult.

The runtime generalises the legacy ``ToolRegistry.invoke`` (pydantic
``model_validate`` → handler) into the full harness chain. It never makes
business decisions: whether a tool call is *legal* for the current agent turn
(0 vs 1 call, which control signal) is the AgentRuntime's decision contract;
whether a tool may run *right now* given execution state is the registry's
Layer-3 precondition, surfaced here as ``PRECONDITION_FAILED``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from styleforge.agentic.hooks.manager import HookManager, ON_TOOL_ERROR, POST_TOOL_USE, PRE_TOOL_USE
from styleforge.agentic.runtime.capability_registry import CapabilityRegistry

# ToolCallResult.status values — a fact the Agent observes, never a crash.
STATUS_OK = "ok"
STATUS_PRECONDITION_FAILED = "precondition_failed"
STATUS_ERROR = "error"


class ToolContext:
    """Execution-time view of the Execution State for stateful handlers.

    Handlers *read* state through ``get`` and return write-backs through
    ``ToolCallResult.state_updates``; they never mutate ``self._state``
    directly. There is exactly one place that persists changes — the graph
    node that calls ToolRuntime — so there is no second state source
    (frozen #2). Runtime dependencies (connection, providers) stay captured
    in the handler closure, never in the serializable state.
    """

    __slots__ = ("_state",)

    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state

    def get(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)


@dataclass
class ToolCallResult:
    """Normalized outcome of one tool execution, returned to the Agent."""

    observation: str
    state_updates: dict[str, Any] = field(default_factory=dict)
    status: str = STATUS_OK


class ToolRuntime:
    def __init__(
        self,
        registry: CapabilityRegistry,
        hooks: HookManager | None = None,
    ) -> None:
        self.registry = registry
        self.hooks = hooks or HookManager()

    def check_precondition(self, name: str, state: dict[str, Any]) -> str | None:
        """Layer 3: ``None`` means runnable, a string is the failure reason."""
        return self.registry.check_precondition(name, state)

    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        state: dict[str, Any],
    ) -> ToolCallResult:
        """Run one tool call and normalize the result for the Agent loop."""
        if not self.registry.contains(name):
            return self._fail(name, STATUS_ERROR, f"未注册的工具：{name}")

        self.hooks.trigger(PRE_TOOL_USE, {"tool": name, "arguments": arguments})

        # Layer 3 — temporary execution conditions (never schema removal).
        precondition = self.registry.check_precondition(name, state)
        if precondition is not None:
            result = ToolCallResult(
                observation=precondition,
                status=STATUS_PRECONDITION_FAILED,
            )
            self.hooks.trigger(
                POST_TOOL_USE,
                {
                    "tool": name,
                    "observation": precondition,
                    "status": STATUS_PRECONDITION_FAILED,
                },
            )
            return result

        capability = self.registry.capability(name)
        try:
            validated = capability.input_model.model_validate(arguments)
        except ValidationError as error:
            return self._fail(
                name,
                STATUS_ERROR,
                f"工具参数不合法（{name}）：{error}",
            )

        try:
            outcome = capability.handler(validated, ToolContext(state))
        except Exception as error:  # noqa: BLE001 — normalize every failure into a fact
            return self._fail(name, STATUS_ERROR, f"工具执行失败（{name}）：{type(error).__name__}: {error}")

        if not isinstance(outcome, ToolCallResult):
            outcome = ToolCallResult(observation=str(outcome))
        self.hooks.trigger(
            POST_TOOL_USE,
            {
                "tool": name,
                "observation": outcome.observation,
                "status": outcome.status,
                "state_updates": outcome.state_updates,
            },
        )
        return outcome

    def _fail(self, name: str, status: str, observation: str) -> ToolCallResult:
        self.hooks.trigger(ON_TOOL_ERROR, {"tool": name, "error": observation})
        return ToolCallResult(observation=observation, status=status)
