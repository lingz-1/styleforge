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
import logging
from time import perf_counter
from typing import Any

from pydantic import ValidationError

from styleforge.agentic.hooks.manager import HookManager, ON_TOOL_ERROR, POST_TOOL_USE, PRE_TOOL_USE
from styleforge.agentic.runtime.capability_registry import CapabilityRegistry
from styleforge.agentic.context.prompt_security import validate_outbound_tool_arguments
from styleforge.common.errors import ErrorCode
from styleforge.common.observability import observability


logger = logging.getLogger(__name__)

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
    error_code: str | None = None
    retryable: bool = False


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
        started_at = perf_counter()

        def observed(result: ToolCallResult) -> ToolCallResult:
            observability.record_operation(
                "tool_call",
                success=result.status == STATUS_OK,
                duration_ms=(perf_counter() - started_at) * 1000.0,
                retryable=result.retryable,
                error_code=result.error_code or "",
                component="tool_runtime",
                tool=name,
                run_id=str(state.get("run_id") or ""),
            )
            return result

        if not self.registry.contains(name):
            return observed(
                self._fail(
                    name,
                    STATUS_ERROR,
                    f"未注册的工具：{name}",
                    error_code=ErrorCode.TOOL_NOT_FOUND,
                )
            )

        capability = self.registry.capability(name)
        try:
            validated = capability.input_model.model_validate(arguments)
        except ValidationError as error:
            return observed(
                self._fail(
                    name,
                    STATUS_ERROR,
                    f"工具参数不合法（{name}）：{error}",
                    error_code=ErrorCode.TOOL_INPUT_INVALID,
                )
            )

        outbound_violation = validate_outbound_tool_arguments(name, validated)
        if outbound_violation is not None:
            return observed(
                self._fail(
                    name,
                    STATUS_ERROR,
                    "外部工具调用已被 Prompt Security 策略阻止",
                    error_code=ErrorCode.PROMPT_INJECTION_BLOCKED,
                )
            )

        # Hooks only receive arguments after schema and prompt-security checks;
        # rejected external payloads must not escape through telemetry hooks.
        self.hooks.trigger(PRE_TOOL_USE, {"tool": name, "arguments": validated.model_dump()})

        # Layer 3 — temporary execution conditions (never schema removal).
        precondition = self.registry.check_precondition(name, state)
        if precondition is not None:
            result = ToolCallResult(
                observation=precondition,
                status=STATUS_PRECONDITION_FAILED,
                error_code=ErrorCode.TOOL_PRECONDITION_FAILED.value,
            )
            self.hooks.trigger(
                POST_TOOL_USE,
                {
                    "tool": name,
                    "observation": precondition,
                    "status": STATUS_PRECONDITION_FAILED,
                    "error_code": result.error_code,
                    "retryable": False,
                },
            )
            return observed(result)

        try:
            outcome = capability.handler(validated, ToolContext(state))
        except Exception as error:  # noqa: BLE001 — normalize every failure into a fact
            if isinstance(error, TimeoutError):
                code = ErrorCode.TOOL_TIMEOUT
                retryable = True
            elif isinstance(error, ConnectionError):
                code = ErrorCode.TOOL_UNAVAILABLE
                retryable = True
            else:
                code = ErrorCode.TOOL_EXECUTION_FAILED
                retryable = False
            logger.exception(
                "tool_execution_failed tool=%s code=%s error_type=%s",
                name,
                code.value,
                type(error).__name__,
            )
            return observed(
                self._fail(
                    name,
                    STATUS_ERROR,
                    f"工具执行失败（{name}）：{type(error).__name__}: {error}",
                    error_code=code,
                    retryable=retryable,
                )
            )

        if not isinstance(outcome, ToolCallResult):
            outcome = ToolCallResult(observation=str(outcome))
        self.hooks.trigger(
            POST_TOOL_USE,
            {
                "tool": name,
                "observation": outcome.observation,
                "status": outcome.status,
                "state_updates": outcome.state_updates,
                "error_code": outcome.error_code,
                "retryable": outcome.retryable,
            },
        )
        return observed(outcome)

    def _fail(
        self,
        name: str,
        status: str,
        observation: str,
        *,
        error_code: ErrorCode,
        retryable: bool = False,
    ) -> ToolCallResult:
        self.hooks.trigger(
            ON_TOOL_ERROR,
            {
                "tool": name,
                "error": observation,
                "error_code": error_code.value,
                "retryable": retryable,
            },
        )
        return ToolCallResult(
            observation=observation,
            status=status,
            error_code=error_code.value,
            retryable=retryable,
        )
