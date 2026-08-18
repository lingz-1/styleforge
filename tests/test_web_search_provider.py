"""TavilySearchProvider: structured results, truncation, and graceful degradation.

The provider never raises: no key, a network failure, or an empty result is
returned as a ``WebSearchResult`` *fact* so the Agent loop keeps working. The
network call is an injectable transport (same pattern as the weather provider),
so every test is offline.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.request import Request

from styleforge.tools.web_search import TavilySearchProvider


def _fake_transport(payload: dict[str, Any], *, captured: list | None = None):
    """Return a transport that returns ``payload`` and records the request."""

    def _fake(request: Request, timeout: float) -> dict[str, Any]:
        if captured is not None:
            captured.append({"request": request, "timeout": timeout})
        return payload

    return _fake


def _header(request: Request, name: str) -> str:
    """Read a header case-insensitively (urllib's key casing varies by version)."""
    lower = name.lower()
    for key, value in request.headers.items():
        if key.lower() == lower:
            return value
    return ""


def _hit(title: str = "标题", content: str = "内容", url: str = "https://x.example/1") -> dict:
    return {"title": title, "content": content, "url": url}


def test_search_parses_results_and_truncates_content() -> None:
    long_content = "字" * 500
    provider = TavilySearchProvider(
        api_key="tvly-test",
        transport=_fake_transport({"results": [_hit(content=long_content)]}),
    )
    result = provider.search("海边度假穿什么")

    assert result.available is True
    assert result.error is None
    assert len(result.results) == 1
    assert result.results[0].title == "标题"
    assert result.results[0].url == "https://x.example/1"
    assert len(result.results[0].content) == 200


def test_search_no_key_degrades_without_network() -> None:
    def _explode(_request: Request, _timeout: float) -> dict[str, Any]:
        raise AssertionError("transport must not be called without a key")

    provider = TavilySearchProvider(api_key="", transport=_explode)
    result = provider.search("海边度假穿什么")

    assert result.available is False
    assert result.error
    assert result.results == []


def test_search_transport_failure_returns_error_not_raise() -> None:
    def _explode(_request: Request, _timeout: float) -> dict[str, Any]:
        raise TimeoutError("timed out")

    provider = TavilySearchProvider(api_key="tvly-test", transport=_explode)
    result = provider.search("海边度假穿什么")

    assert result.available is True  # configured, the *call* failed
    assert "联网搜索失败" in result.error
    assert result.results == []


def test_search_empty_results() -> None:
    provider = TavilySearchProvider(
        api_key="tvly-test", transport=_fake_transport({"results": []})
    )
    result = provider.search("不存在的东西")

    assert result.results == []
    assert result.error is None


def test_search_sends_auth_body_and_limits() -> None:
    captured: list[dict] = []
    provider = TavilySearchProvider(
        api_key="tvly-secret",
        timeout=3.0,
        max_results=4,
        transport=_fake_transport({"results": [_hit()] * 6}, captured=captured),
    )
    provider.search(" 商务晚宴着装 ")

    assert len(captured) == 1
    request = captured[0]["request"]
    assert captured[0]["timeout"] == 3.0
    assert _header(request, "Authorization") == "Bearer tvly-secret"
    assert _header(request, "Content-Type") == "application/json"
    assert "api.tavily.com" in request.full_url
    body = json.loads(request.data)
    assert body["query"] == "商务晚宴着装"  # trimmed, capped at 200
    assert body["max_results"] == 4
    assert body["search_depth"] == "basic"


def test_search_caps_results_at_max_results() -> None:
    provider = TavilySearchProvider(
        api_key="tvly-test",
        max_results=3,
        transport=_fake_transport({"results": [_hit() for _ in range(8)]}),
    )
    result = provider.search("潮流趋势")

    assert len(result.results) == 3


def test_search_answer_passthrough() -> None:
    provider = TavilySearchProvider(
        api_key="tvly-test",
        transport=_fake_transport({"results": [], "answer": "快干材质更适合海边"}),
    )
    result = provider.search("海边度假穿什么", include_answer=True)

    assert result.answer == "快干材质更适合海边"
