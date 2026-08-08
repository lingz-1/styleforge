"""DeepSeek OpenAI-compatible chat client with structured JSON output.

The client keeps all SDK imports inside call paths so importing this module
never fails when ``openai`` is not installed (offline/demo environments still
work with the deterministic fallback).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
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


class LlmChatClient(Protocol):
    """A minimal JSON-mode chat contract shared by agents and fakes."""

    def chat_json(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict[str, Any],
        temperature: float = 0.2,
    ) -> tuple[dict[str, Any], LlmCallDiagnostics]: ...


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
