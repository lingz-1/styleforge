"""A scripted fake LLM client shared by agent and workflow tests.

Implements the ``LlmChatClient`` protocol without touching any network. Each
call pops the next script entry: a dict (returned as parsed JSON) or an
exception class/instance (raised verbatim).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from styleforge.llm.client import LlmCallDiagnostics, LlmUnavailable


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
