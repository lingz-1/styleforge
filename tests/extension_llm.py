from __future__ import annotations

import json
from typing import Any

from styleforge.llm.client import LlmCallDiagnostics, ToolUseBlock

# A chat_tools script entry: decision dict alone, or (decision, tool_specs).
ToolEntry = dict[str, Any] | tuple[dict[str, Any], list[dict[str, Any]]]


class ScriptedExtensionLlm:
    def __init__(self, responses: list[ToolEntry]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def chat_json(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict[str, Any],
        temperature: float = 0.2,
    ) -> tuple[dict[str, Any], LlmCallDiagnostics]:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "json_schema": json_schema,
                "temperature": temperature,
            }
        )
        if not self.responses:
            raise AssertionError("Unexpected extension LLM call")
        return self.responses.pop(0), LlmCallDiagnostics(
            model="scripted-extension-test",
            prompt_version="extension-three-agent-v3.1",
            latency_ms=1,
            prompt_tokens=10,
            completion_tokens=10,
            retries=0,
        )

    def chat_tools(
        self,
        *,
        system: str,
        user: str,
        tools: list[Any],
        tool_choice: str = "auto",
        temperature: float = 0.2,
    ) -> tuple[str, list[ToolUseBlock], LlmCallDiagnostics]:
        """Scripted twin: dict → decision only; tuple → (decision, tool_specs)."""
        self.calls.append(
            {
                "system": system,
                "user": user,
                "tools": tools,
                "tool_choice": tool_choice,
                "temperature": temperature,
            }
        )
        if not self.responses:
            raise AssertionError("Unexpected extension LLM call")
        entry = self.responses.pop(0)
        if isinstance(entry, tuple) and len(entry) == 2:
            decision, tool_specs = entry
        elif isinstance(entry, dict):
            decision, tool_specs = entry, []
        else:
            raise TypeError(f"unsupported extension script entry: {type(entry).__name__}")
        blocks = [
            ToolUseBlock(
                name=spec["name"],
                arguments=spec.get("arguments", {}),
                arg_json=json.dumps(spec.get("arguments", {}), ensure_ascii=False),
            )
            for spec in tool_specs
        ]
        decision_text = json.dumps(decision, ensure_ascii=False)
        return decision_text, blocks, LlmCallDiagnostics(
            model="scripted-extension-test",
            prompt_version="extension-three-agent-v3.1",
            latency_ms=1,
            prompt_tokens=10,
            completion_tokens=10,
            retries=0,
        )


def intent_response(summary: str) -> dict[str, Any]:
    return {
        "intent_summary": summary,
        "target_style": "",
        "target_occasion": "",
        "target_item_terms": [],
        "optional_context": [],
    }


def approved_review(summary: str = "方案有事实依据并满足任务约束") -> dict[str, Any]:
    return {
        "approved": True,
        "grounded": True,
        "summary": summary,
        "issues": [],
    }
