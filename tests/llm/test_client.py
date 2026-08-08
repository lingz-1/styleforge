import json
from types import SimpleNamespace

import pytest

from styleforge.llm.client import DeepSeekClient, LlmUnavailable
from styleforge.llm.schema import CriticOutput, parse_llm_json


class _FakeMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str | None) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str | None) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, responses, *, fail_response_format: bool = False) -> None:
        self.responses = list(responses)
        self.fail_response_format = fail_response_format
        self.requests: list[dict] = []

    def create(self, **kwargs) -> _FakeResponse:
        self.requests.append(kwargs)
        if self.fail_response_format and kwargs.get("response_format"):
            raise RuntimeError("response_format is not supported")
        content = self.responses.pop(0) if self.responses else None
        return _FakeResponse(content)


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        # Mirrors the openai SDK navigation client.chat.completions.create(...).
        self.chat = SimpleNamespace(completions=completions)


def _make_client(completions: _FakeCompletions, max_retries: int = 2) -> DeepSeekClient:
    def factory(*, api_key: str, base_url: str) -> _FakeChat:
        return _FakeChat(completions)

    return DeepSeekClient(api_key="test-key", max_retries=max_retries, client_factory=factory)


def test_chat_json_returns_parsed_payload() -> None:
    payload = {"decision": "accept"}
    completions = _FakeCompletions([json.dumps(payload)])
    client = _make_client(completions)

    parsed, diagnostics = client.chat_json(system="JSON", user="hi", json_schema={})

    assert parsed == payload
    assert diagnostics.retries == 0
    assert diagnostics.latency_ms >= 0


def test_chat_json_retries_on_invalid_json_then_succeeds() -> None:
    completions = _FakeCompletions(["not json", json.dumps({"decision": "accept"})])
    client = _make_client(completions, max_retries=2)

    parsed, diagnostics = client.chat_json(system="JSON", user="hi", json_schema={})

    assert parsed == {"decision": "accept"}
    assert diagnostics.retries == 1
    assert len(completions.requests) == 2


def test_chat_json_raises_after_all_invalid_attempts() -> None:
    completions = _FakeCompletions(["nope", "still nope", "nope"])
    client = _make_client(completions, max_retries=2)

    with pytest.raises(LlmUnavailable):
        client.chat_json(system="JSON", user="hi", json_schema={})
    assert len(completions.requests) == 3


def test_chat_json_falls_back_when_response_format_unsupported() -> None:
    completions = _FakeCompletions([json.dumps({"decision": "accept"})], fail_response_format=True)
    client = _make_client(completions)

    parsed, _ = client.chat_json(system="JSON", user="hi", json_schema={})

    assert parsed == {"decision": "accept"}
    assert completions.requests[-1].get("response_format") is None


def test_chat_json_raises_on_empty_content() -> None:
    completions = _FakeCompletions([None])
    client = _make_client(completions, max_retries=0)

    with pytest.raises(LlmUnavailable):
        client.chat_json(system="JSON", user="hi", json_schema={})


def test_client_output_passes_schema_parse() -> None:
    payload = {
        "outfit_assessment": {
            "outfit_id": "outfit_002",
            "dimension_scores": {
                "request_relevance": 9,
                "request_specificity": 8,
                "outfit_coordination": 9,
                "wearability": 8,
                "freshness": 7,
            },
            "reasoning": "ok",
            "improvements": "",
        },
        "explanation_assessment": {"grounded": True, "unsupported_claims": []},
        "alternatives": [],
        "decision": "accept",
        "failure_source": "",
        "feedback": "",
        "missing_items": [],
        "best_effort_outfit_id": "",
    }
    completions = _FakeCompletions([json.dumps(payload)])
    client = _make_client(completions)

    parsed, _ = client.chat_json(system="JSON", user="hi", json_schema={})
    model = parse_llm_json(json.dumps(parsed), CriticOutput)

    assert model.decision == "accept"
