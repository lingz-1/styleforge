"""Critic node: the Main-Graph gate that judges a candidate semantically.

The Critic runs ``chat_json`` — but it is NOT a hand-crafted prompt: it takes
its bundle from the shared ``PromptAssembler`` (agent="critic") exactly like the
tool-calling agents (frozen #17). The same Visibility / Stable Prefix / Guard
cover this model call too.

Inputs come from the critic visibility view: user request + research evidence +
saved candidates + the draft under review. It judges intent fidelity, outfit
quality and incremental diversity (see instructions/critic.md) and returns a
``ReviewResult``; the Main Graph routes on ``approved``.
"""

from __future__ import annotations

from typing import Any

from styleforge.agentic.runtime.agent_runtime import AgentRuntime, ContextLimitError
from styleforge.agentic.tools.local_tools import AGENT_CRITIC
from styleforge.models.agentic_contract import ReviewResult

# chat_json contract — identical shape to the legacy reviewer gate (frozen: the
# Critic still speaks the structured-output protocol).
_CRITIC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "approved": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "feedback": {"type": "string"},
    },
    "required": ["approved", "issues", "feedback"],
    "additionalProperties": False,
}


def make_critic_node(runtime: AgentRuntime):
    """A Main-Graph node over one AgentRuntime (runtime deps stay in the harness)."""

    def critic_node(state: dict[str, Any]) -> dict[str, Any]:
        bundle = runtime.assemble_bundle(AGENT_CRITIC, dict(state), tools=[])
        guard = runtime.guard.check(bundle)
        if guard.bundle is None:
            raise ContextLimitError(
                f"context guard: {guard.status}: {'；'.join(guard.warnings)}"
            )
        payload, _ = runtime.llm.chat_json(
            system=guard.bundle.system_text,
            user=guard.bundle.user_message,
            json_schema=_CRITIC_SCHEMA,
        )
        return {"critic_result": ReviewResult.model_validate(payload)}

    return critic_node
