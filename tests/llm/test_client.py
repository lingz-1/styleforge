import json
from types import SimpleNamespace

import pytest

from styleforge.llm.client import (
    DeepSeekClient,
    LlmUnavailable,
    ToolDefinition,
    ToolUseBlock,
)
from styleforge.llm.schema import CriticOutput, parse_llm_json


class _FakeToolFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, name: str, arguments: str) -> None:
        self.function = _FakeToolFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content: str | None, tool_calls: list[_FakeToolCall] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(
        self, content: str | None, tool_calls: list[_FakeToolCall] | None = None
    ) -> None:
        self.message = _FakeMessage(content, tool_calls)


class _FakeResponse:
    def __init__(
        self, content: str | None, tool_calls: list[_FakeToolCall] | None = None
    ) -> None:
        self.choices = [_FakeChoice(content, tool_calls)]


class _FakeCompletions:
    def __init__(
        self,
        responses,
        *,
        fail_response_format: bool = False,
        fail_tools: bool = False,
    ) -> None:
        self.responses = list(responses)
        self.fail_response_format = fail_response_format
        self.fail_tools = fail_tools
        self.requests: list[dict] = []

    def create(self, **kwargs) -> _FakeResponse:
        self.requests.append(kwargs)
        if self.fail_response_format and kwargs.get("response_format"):
            raise RuntimeError("response_format is not supported")
        if self.fail_tools and kwargs.get("tools"):
            raise RuntimeError("tools is not supported")
        entry = self.responses.pop(0) if self.responses else None
        if isinstance(entry, tuple):
            content, tool_calls = entry
        else:
            content, tool_calls = entry, None
        return _FakeResponse(content, tool_calls)


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


def _tool_definition(name: str = "search_web") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="查询实时外部信息",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )


def test_chat_tools_returns_decision_and_tool_use() -> None:
    decision = {"control": "CONTINUE"}
    tool_call = _FakeToolCall("search_web", json.dumps({"query": "北京 天气"}))
    completions = _FakeCompletions([(json.dumps(decision), [tool_call])])
    client = _make_client(completions)

    text, blocks, diagnostics = client.chat_tools(
        system="S", user="hi", tools=[_tool_definition()]
    )

    assert json.loads(text) == decision
    assert len(blocks) == 1
    assert isinstance(blocks[0], ToolUseBlock)
    assert blocks[0].name == "search_web"
    assert blocks[0].arguments == {"query": "北京 天气"}
    assert diagnostics.retries == 0
    sent = completions.requests[0]
    assert sent["tools"][0]["type"] == "function"
    assert sent["tools"][0]["function"]["name"] == "search_web"
    assert sent["tool_choice"] == "auto"


def test_chat_tools_allows_zero_tool_calls() -> None:
    decision = {"control": "CANDIDATE_READY", "candidate_id": "c1"}
    completions = _FakeCompletions([json.dumps(decision)])
    client = _make_client(completions)

    text, blocks, _ = client.chat_tools(system="S", user="hi", tools=[])

    assert json.loads(text) == decision
    assert blocks == []


def test_chat_tools_retries_when_decision_block_missing() -> None:
    # First turn: a tool call but no content (model omitted the decision block).
    tool_call = _FakeToolCall("update_plan", json.dumps({"objective": "x"}))
    decision = {"control": "CONTINUE"}
    completions = _FakeCompletions([(None, [tool_call]), json.dumps(decision)])
    client = _make_client(completions, max_retries=2)

    text, blocks, diagnostics = client.chat_tools(system="S", user="hi", tools=[])

    assert json.loads(text) == decision
    assert blocks == []
    assert diagnostics.retries == 1
    assert len(completions.requests) == 2


def test_chat_tools_retries_on_malformed_tool_arguments() -> None:
    decision = {"control": "CONTINUE"}
    bad = _FakeToolCall("search_web", "{not json")
    good = _FakeToolCall("search_web", json.dumps({"query": "北京 天气"}))
    completions = _FakeCompletions(
        [(json.dumps(decision), [bad]), (json.dumps(decision), [good])]
    )
    client = _make_client(completions, max_retries=2)

    text, blocks, diagnostics = client.chat_tools(system="S", user="hi", tools=[])

    assert blocks[0].name == "search_web"
    assert blocks[0].arguments == {"query": "北京 天气"}
    assert blocks[0].arg_json == json.dumps({"query": "北京 天气"})
    assert diagnostics.retries == 1


def test_chat_tools_raises_after_all_invalid_attempts() -> None:
    bad = (None, [_FakeToolCall("search_web", "{}")])  # tool call, no decision block
    completions = _FakeCompletions([bad, bad, bad])
    client = _make_client(completions, max_retries=2)

    with pytest.raises(LlmUnavailable):
        client.chat_tools(system="S", user="hi", tools=[])
    assert len(completions.requests) == 3


def test_chat_tools_strips_code_fence_from_decision_block() -> None:
    completions = _FakeCompletions(['```json\n{"control": "CONTINUE"}\n```'])
    client = _make_client(completions)

    text, blocks, _ = client.chat_tools(system="S", user="hi", tools=[])

    assert json.loads(text) == {"control": "CONTINUE"}
    assert blocks == []


def test_chat_tools_degrades_when_tools_unsupported() -> None:
    decision = {"control": "CONTINUE"}
    completions = _FakeCompletions([json.dumps(decision)], fail_tools=True)
    client = _make_client(completions)

    text, blocks, diagnostics = client.chat_tools(
        system="S", user="hi", tools=[_tool_definition()]
    )

    assert json.loads(text) == decision
    assert blocks == []
    assert diagnostics.degraded_reason
    # The fallback call dropped the tools parameter.
    assert completions.requests[-1].get("tools") is None
