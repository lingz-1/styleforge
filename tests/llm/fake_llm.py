"""A scripted fake LLM client shared by agent and workflow tests.

Implements the ``LlmChatClient`` protocol without touching any network. Each
call pops the next script entry: a dict (returned as parsed JSON) or an
exception class/instance (raised verbatim).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from styleforge.llm.client import LlmCallDiagnostics, LlmUnavailable, ToolUseBlock


class FakeLlm:
    """Scripted fake ``LlmChatClient``.

    Args:
        script: entries consumed in order. A ``dict`` is returned as the
            parsed payload; a ``str`` is parsed with ``json.loads``; anything
            raising (exception class or instance) is raised instead.
        fail_after: if set, every call after this many successful calls
            raises ``LlmUnavailable``.
    """

    def __init__(
        self,
        script: Sequence[dict[str, Any] | str | Exception | type[Exception]],
        fail_after: int | None = None,
    ) -> None:
        self.script = list(script)
        self.fail_after = fail_after
        self.calls: list[dict[str, Any]] = []
        self.call_count = 0

    def chat_json(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict[str, Any],
        temperature: float = 0.2,
    ) -> tuple[dict[str, Any], LlmCallDiagnostics]:
        self.calls.append(
            {"system": system, "user": user, "json_schema": json_schema, "temperature": temperature}
        )
        if self.fail_after is not None and self.call_count >= self.fail_after:
            raise LlmUnavailable("fake LLM unavailable")
        entry = self.script[self.call_count]
        self.call_count += 1
        if isinstance(entry, dict):
            payload = entry
        elif isinstance(entry, str):
            payload = json.loads(entry)
        elif isinstance(entry, type) and issubclass(entry, Exception):
            raise entry
        elif isinstance(entry, Exception):
            raise entry
        else:
            raise TypeError(f"unsupported fake script entry: {type(entry).__name__}")
        diagnostics = LlmCallDiagnostics(
            model="fake",
            prompt_version="test",
            latency_ms=1,
            prompt_tokens=0,
            completion_tokens=0,
            retries=0,
        )
        return payload, diagnostics

    def chat_tools(
        self,
        *,
        system: str,
        user: str,
        tools: list[Any],
        tool_choice: str = "auto",
        temperature: float = 0.2,
    ) -> tuple[str, list[ToolUseBlock], LlmCallDiagnostics]:
        """Scripted twin of ``chat_json`` for the same shared script.

        A script entry may be:
          * ``dict``            → decision block only (zero tool calls),
          * ``(dict, list)``    → ``(decision, tool_specs)`` where each spec is
                                  ``{"name": ..., "arguments": {...}}``,
          * ``str``             → decision block parsed as JSON,
          * an exception        → raised verbatim.
        The decision block is JSON-serialised back to text because
        ``chat_tools`` hands the AgentRuntime raw decision text.
        """
        self.calls.append(
            {
                "system": system,
                "user": user,
                "tools": tools,
                "tool_choice": tool_choice,
                "temperature": temperature,
            }
        )
        if self.fail_after is not None and self.call_count >= self.fail_after:
            raise LlmUnavailable("fake LLM unavailable")
        entry = self.script[self.call_count]
        self.call_count += 1
        if isinstance(entry, tuple) and len(entry) == 2:
            decision, tool_specs = entry
        elif isinstance(entry, str):
            decision = json.loads(entry)
            tool_specs = []
        elif isinstance(entry, dict):
            decision = entry
            tool_specs = []
        elif isinstance(entry, type) and issubclass(entry, Exception):
            raise entry
        elif isinstance(entry, Exception):
            raise entry
        else:
            raise TypeError(f"unsupported fake script entry: {type(entry).__name__}")
        blocks = [
            ToolUseBlock(
                name=spec["name"],
                arguments=spec.get("arguments", {}),
                arg_json=json.dumps(spec.get("arguments", {}), ensure_ascii=False),
            )
            for spec in tool_specs
        ]
        decision_text = json.dumps(decision, ensure_ascii=False)
        return decision_text, blocks, fake_diagnostics()


def fake_diagnostics(**overrides: Any) -> LlmCallDiagnostics:
    return LlmCallDiagnostics(
        model="fake",
        prompt_version="test",
        latency_ms=1,
        prompt_tokens=0,
        completion_tokens=0,
        retries=0,
        **overrides,
    )


# Optional typing helper so fakes can be annotated as the protocol.
FakeFactory = Callable[..., FakeLlm]
