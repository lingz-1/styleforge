"""DeepSeek OpenAI-compatible chat client with structured JSON output.

The client keeps all SDK imports inside call paths so importing this module
never fails when ``openai`` is not installed (offline/demo environments still
work with the deterministic fallback).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from styleforge.core.config import Settings


class LlmUnavailable(Exception):
    """Provider, network, authentication, rate-limit, or 5xx failure."""


class LlmInvalidJson(Exception):
    """Provider returned text that cannot be parsed as JSON."""


class LlmSchemaViolation(Exception):
    """Provider returned valid JSON that fails the schema contract."""


@dataclass(frozen=True, slots=True)
class LlmCallDiagnostics:
    model: str
    prompt_version: str
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    retries: int
    degraded_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "prompt_version": self.prompt_version,
            "latency_ms": self.latency_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "retries": self.retries,
            "degraded_reason": self.degraded_reason,
        }


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Wire-level tool description handed to a tool-calling provider.

    ``input_schema`` is the canonical JSON Schema (the same object the
    CapabilityRegistry validates calls against). The Prompt layer carries only
    the one-line capability manifest; the full schema rides exclusively here
    in ``tools=``, so the model never sees two drifted copies of it.
    """

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)

    def to_openai_dict(self) -> dict[str, Any]:
        """OpenAI-compatible ``tools=[...]`` entry for ``chat.completions``."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


@dataclass(frozen=True, slots=True)
class ToolUseBlock:
    """One native tool call parsed from a tool-calling response.

    ``arguments`` is the parsed dict; ``arg_json`` keeps the provider's raw
    string for observability / re-parse. Frozen, so it can ride LangGraph
    state and hashes (stable_prefix_fingerprint) safely.
    """

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    arg_json: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "arguments": self.arguments}


class LlmChatClient(Protocol):
    """A minimal JSON-mode chat contract shared by agents and fakes.

    Two capabilities coexist on purpose (frozen architecture decision #3):

    * ``chat_json`` — structured output. Kept byte-for-byte identical to the
      original contract; the 9 legacy call sites (critic / judge / memory
      extractor / legacy graphs) never change.
    * ``chat_tools`` — native tool-calling (Coordinator / Research / Stylist).
      Returns the model's *decision block* (raw text, parsed per-Agent by the
      AgentRuntime) plus parsed ``ToolUseBlock`` tool calls. Business rules
      like "CONTINUE must carry exactly one tool call" are the AgentRuntime's
      job, never this client's — the client only parses legal native calls.
    """

    def chat_json(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict[str, Any],
        temperature: float = 0.2,
    ) -> tuple[dict[str, Any], LlmCallDiagnostics]: ...

    def chat_tools(
        self,
        *,
        system: str,
        user: str,
        tools: list[ToolDefinition],
        tool_choice: str = "auto",
        temperature: float = 0.2,
    ) -> tuple[str, list[ToolUseBlock], LlmCallDiagnostics]: ...


def llm_client_from_settings(settings: Settings) -> DeepSeekClient | None:
    """Return a configured client, or ``None`` when no API key is set."""
    if not settings.deepseek_api_key.strip():
        return None
    return DeepSeekClient(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        timeout=settings.deepseek_timeout,
        max_retries=settings.deepseek_max_retries,
    )


class DeepSeekClient:
    """OpenAI-compatible chat client against DeepSeek's JSON mode."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        timeout: float = 60.0,
        max_retries: int = 2,
        client_factory: Any = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("deepseek_api_key cannot be empty")
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        # client_factory lets tests inject a fake OpenAI client.
        self._client_factory = client_factory
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            if self._client_factory is not None:
                self._client = self._client_factory(api_key=self.api_key, base_url=self.base_url)
            else:
                from openai import OpenAI

                self._client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout)
        return self._client

    def chat_json(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict[str, Any],
        temperature: float = 0.2,
    ) -> tuple[dict[str, Any], LlmCallDiagnostics]:
        started = time.perf_counter()
        attempts = 0
        raw = ""
        parse_failed = ""
        schema_failed = ""
        while attempts <= self.max_retries:
            attempts += 1
            user_payload = user
            if parse_failed:
                user_payload = (
                    f"{user}\n\n上次输出未通过 JSON 解析，请只输出合法 JSON。"
                    f"解析错误：{parse_failed}"
                )
            elif schema_failed:
                user_payload = (
                    f"{user}\n\n上次 JSON 通过解析但未通过结构校验，请修正字段。"
                    f"校验错误：{schema_failed}"
                )
            try:
                raw = self._request_chat(system, user_payload, temperature)
                try:
                    parsed = json.loads(raw)
                except (json.JSONDecodeError, TypeError) as error:
                    parse_failed = f"{type(error).__name__}: {error}"
                    continue
                if not isinstance(parsed, dict):
                    parse_failed = f"JSON top-level value must be an object, got {type(parsed).__name__}"
                    continue
                # Schema validation is delegated to the caller (pydantic model).
                # The client only guarantees parseable JSON; schema-level retries
                # happen through LlmSchemaViolation propagated by the agent.
                latency_ms = int((time.perf_counter() - started) * 1000)
                return parsed, LlmCallDiagnostics(
                    model=self.model,
                    prompt_version="",
                    latency_ms=latency_ms,
                    prompt_tokens=0,
                    completion_tokens=0,
                    retries=attempts - 1,
                )
            except LlmInvalidJson:
                raise
            except LlmSchemaViolation:
                raise
            except Exception as error:
                # Network / 5xx / auth / rate-limit: raise after retries exhausted.
                if attempts > self.max_retries:
                    raise LlmUnavailable(
                        f"provider call failed after {attempts} attempts: {type(error).__name__}: {error}"
                    ) from error
        raise LlmUnavailable("provider call failed before returning a result")

    def chat_tools(
        self,
        *,
        system: str,
        user: str,
        tools: list[ToolDefinition],
        tool_choice: str = "auto",
        temperature: float = 0.2,
    ) -> tuple[str, list[ToolUseBlock], LlmCallDiagnostics]:
        """Native tool-calling: returns ``(decision_block, tool_uses, diagnostics)``.

        The model answers through two channels: a text decision block
        (``content``, per-Agent JSON the AgentRuntime parses) and native tool
        calls. This client only guarantees a *parseable* response:

        * ``content`` is present and non-empty (after stripping a `````` fence),
        * every ``tool_calls`` entry has a name and a dict-parsable ``arguments``.

        It deliberately allows 0 or 1 tool call — whether that is *legal* for
        the current agent (CONTINUE needs exactly one; CANDIDATE_READY needs
        zero) is the AgentRuntime's decision contract, not this client's.
        """
        started = time.perf_counter()
        attempts = 0
        parse_failed = ""
        degraded_reason = ""
        while attempts <= self.max_retries:
            attempts += 1
            user_payload = user
            if parse_failed:
                user_payload = f"{user}\n\n上次输出未通过解析，请修正。{parse_failed}"
            try:
                content, tool_calls, degraded_reason = self._request_tools(
                    system, user_payload, tools, tool_choice, temperature
                )
            except LlmInvalidJson:
                raise
            except LlmSchemaViolation:
                raise
            except Exception as error:
                # Network / 5xx / auth / rate-limit: raise after retries exhausted.
                if attempts > self.max_retries:
                    raise LlmUnavailable(
                        f"provider tool-call request failed after {attempts} attempts: "
                        f"{type(error).__name__}: {error}"
                    ) from error
                continue
            if content is None or (not content.strip() and not tool_calls):
                # A tool-calling response may legitimately carry an empty
                # ``content`` (OpenAI-compatible providers leave the assistant
                # text blank when the model decides to call a tool). An empty
                # decision block WITH tool calls is therefore a real, usable
                # turn — the AgentRuntime infers the control from the call. Only
                # a completely empty response (no text, no tool call) is a
                # parse failure worth retrying.
                parse_failed = "缺少决策文本块：请同时输出决策 JSON 文本，再附加工具调用。"
                continue
            content = _strip_code_fence(content)
            blocks: list[ToolUseBlock] = []
            failed = False
            for call in tool_calls or []:
                function = _attr(call, "function")
                if function is None:
                    parse_failed = "tool call 缺少 function 字段。"
                    failed = True
                    break
                name = _attr(function, "name", "") or ""
                args_raw = _attr(function, "arguments", "") or ""
                try:
                    arguments = json.loads(args_raw) if args_raw else {}
                except (json.JSONDecodeError, TypeError) as error:
                    parse_failed = f"tool 参数 JSON 解析失败：{type(error).__name__}: {error}"
                    failed = True
                    break
                if not isinstance(arguments, dict):
                    parse_failed = "tool 参数必须是 JSON object。"
                    failed = True
                    break
                blocks.append(ToolUseBlock(name=name, arguments=arguments, arg_json=args_raw))
            if failed:
                continue
            latency_ms = int((time.perf_counter() - started) * 1000)
            return content, blocks, LlmCallDiagnostics(
                model=self.model,
                prompt_version="",
                latency_ms=latency_ms,
                prompt_tokens=0,
                completion_tokens=0,
                retries=attempts - 1,
                degraded_reason=degraded_reason,
            )
        raise LlmUnavailable("provider tool-call request failed before returning a result")

    def _request_tools(
        self,
        system: str,
        user: str,
        tools: list[ToolDefinition],
        tool_choice: str,
        temperature: float,
    ) -> tuple[str | None, list[Any] | None, str]:
        """One provider round-trip with native ``tools``, degrading gracefully.

        Providers without tool-calling support are retried as a plain
        completion (decision block only, zero tool calls); the degradation is
        reported in ``diagnostics.degraded_reason`` so the AgentRuntime can
        decide what zero tool calls means here.
        """
        client = self._get_client()
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        openai_tools = [tool.to_openai_dict() for tool in tools]
        degraded = ""
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                tools=openai_tools,
                tool_choice=tool_choice,
            )
        except Exception as error:
            message_text = str(error)
            if "tools" not in message_text and "tool_choice" not in message_text:
                raise
            # Provider without tool-calling support: plain completion fallback.
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
            )
            degraded = f"tools unsupported: {type(error).__name__}"
        message = response.choices[0].message
        content = _attr(message, "content")
        tool_calls = _attr(message, "tool_calls")
        if content is None and not tool_calls:
            raise LlmUnavailable("provider returned empty completion content")
        return content, tool_calls, degraded

    def _request_chat(self, system: str, user: str, temperature: float) -> str:
        client = self._get_client()
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
        except Exception as error:
            message = str(error)
            if "response_format" in message or "json_object" in message:
                # Provider without JSON mode: retry as plain completion.
                response = client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                )
                content = response.choices[0].message.content
            else:
                raise
        if content is None:
            raise LlmUnavailable("provider returned empty completion content")
        return content


def client_factory_from_settings(settings: Settings) -> DeepSeekClient | None:
    """Compatibility alias kept for call sites that pass Settings directly."""
    return llm_client_from_settings(settings)


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """Read ``obj.name`` regardless of whether ``obj`` is a dict or an object.

    The openai SDK returns response objects in production, while tests inject
    dict-shaped fakes; this helper keeps ``chat_tools`` parsing agnostic.
    """
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _strip_code_fence(text: str) -> str:
    """Strip a leading/trailing `````` block from model content.

    Models sometimes wrap the decision block in markdown fences even inside a
    tool-calling response; the client normalises before the AgentRuntime parses.
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text
