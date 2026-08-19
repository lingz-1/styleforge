"""Hook manager: lifecycle observers around Agent / Tool execution (frozen §7).

The Graph owns the control flow; the HookManager is the extension seam for
cross-cutting concerns (budget counting, observation truncation, error
fallback, handoff audit, tracing) so those never become graph branches.

First-version events:

    before_model     payload: {agent, step_count}      → budget / token counting
    after_model      payload: {agent, diagnostics}     → cost / latency audit
    pre_tool_use     payload: {tool, arguments}        → observation (no veto yet)
    post_tool_use    payload: {tool, observation, status, state_updates}
    on_tool_error    payload: {tool, error}            → error fallback
    before_handoff   payload: {from_agent, to_agent}   → handoff audit
    after_handoff    payload: {from_agent, to_agent, result}
    on_finish        payload: {status, step_count, llm_call_count}

A hook receives the event payload dict (mutable) and returns ``None``. It may
mutate the payload to pass data on to the caller — that is the whole contract.
Hooks must never raise: the harness catches and records the failure so one bad
observer cannot break an agent turn.
"""

from __future__ import annotations

from typing import Any, Callable

HookFn = Callable[[dict[str, Any]], None]

# Event names (strings, so a future observer can subscribe without importing).
BEFORE_MODEL = "before_model"
AFTER_MODEL = "after_model"
PRE_TOOL_USE = "pre_tool_use"
POST_TOOL_USE = "post_tool_use"
ON_TOOL_ERROR = "on_tool_error"
BEFORE_HANDOFF = "before_handoff"
AFTER_HANDOFF = "after_handoff"
ON_FINISH = "on_finish"

_EVENTS = frozenset(
    {
        BEFORE_MODEL,
        AFTER_MODEL,
        PRE_TOOL_USE,
        POST_TOOL_USE,
        ON_TOOL_ERROR,
        BEFORE_HANDOFF,
        AFTER_HANDOFF,
        ON_FINISH,
    }
)


class HookManager:
    def __init__(self) -> None:
        self._hooks: dict[str, list[HookFn]] = {}
        self.errors: list[tuple[str, Exception]] = []

    def register_hook(self, event: str, hook: HookFn) -> None:
        if event not in _EVENTS:
            raise ValueError(f"unknown hook event: {event}")
        self._hooks.setdefault(event, []).append(hook)

    def trigger(self, event: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Run every hook subscribed to ``event`` against ``payload``.

        Payloads are passed by reference so hooks can annotate them; the
        mutated dict is returned for the caller's convenience.
        """
        for hook in self._hooks.get(event, ()):
            try:
                hook(payload)
            except Exception as error:  # noqa: BLE001 — one bad hook never breaks a turn
                self.errors.append((event, error))
        return payload

    def has_event(self, event: str) -> bool:
        return bool(self._hooks.get(event))
