"""Tavily web-search provider with an injectable JSON transport.

Unlike the weather ``JsonTransport`` (GET + query params), Tavily needs POST +
a Bearer token and a JSON body, so the transport receives the fully-built
``urllib.request.Request`` and returns the parsed JSON. The provider never
raises on network/parse failures: it returns a ``WebSearchResult`` carrying an
``error`` fact instead, so the Agent loop can work around an unreachable web.
Without a key the provider is ``available=False`` and ``search`` degrades
without touching the network.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.request import Request, urlopen

from styleforge.models.agentic_contract import WebSearchHit, WebSearchResult

WebSearchTransport = Callable[[Request, float], dict[str, Any]]


def _default_transport(request: Request, timeout: float) -> dict[str, Any]:
    with urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


class TavilySearchProvider:
    """Tavily ``/search`` adapter. ``available`` is false without a key."""

    name = "tavily"
    endpoint = "https://api.tavily.com/search"
    #: Per-hit content cap so a long page never bloats the Agent's context.
    MAX_CONTENT_CHARS = 200

    def __init__(
        self,
        *,
        api_key: str = "",
        timeout: float = 10.0,
        max_results: int = 5,
        transport: WebSearchTransport | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        self.timeout = timeout
        self.max_results = min(max(1, max_results), 20)
        self._transport = transport or _default_transport

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def search(
        self,
        query: str,
        *,
        topic: str = "general",
        include_answer: bool = False,
    ) -> WebSearchResult:
        query = (query or "").strip()[:200]
        if not self.available:
            return WebSearchResult(
                query=query,
                error="联网搜索未配置（TAVILY_API_KEY）",
                available=False,
            )
        body = {
            "query": query,
            "max_results": self.max_results,
            "search_depth": "basic",
            "topic": topic,
            "include_answer": include_answer,
        }
        request = Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "StyleForge/0.5",
            },
        )
        try:
            payload = self._transport(request, self.timeout)
        except Exception as error:  # noqa: BLE001 - provider boundary, fact not crash
            return WebSearchResult(query=query, error=f"联网搜索失败：{error}")
        results = [
            WebSearchHit(
                title=str(item.get("title", ""))[:200],
                url=str(item.get("url", "")),
                content=str(item.get("content", ""))[: self.MAX_CONTENT_CHARS].strip(),
            )
            for item in payload.get("results", [])[: self.max_results]
            if isinstance(item, dict)
        ]
        return WebSearchResult(
            query=query,
            results=results,
            answer=str(payload.get("answer", "") or ""),
        )
