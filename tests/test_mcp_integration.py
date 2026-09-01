"""MCP protocol, safety, fallback, and StyleForge server integration tests."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from styleforge.agentic.environment import Environment
from styleforge.integrations.mcp.client import (
    McpClient,
    McpClientError,
    McpServerConfig,
    McpToolResponse,
)
from styleforge.integrations.mcp.telemetry import capture_mcp_traces, mcp_runtime
from styleforge.integrations.mcp.weather import (
    McpFetchJsonTransport,
    McpOpenMeteoProvider,
)
from styleforge.mcp_server import STYLEFORGE_MCP_TOOL_NAMES, styleforge_mcp
from styleforge.repositories.catalog_repository import upsert_items
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.wardrobe_repository import add_items
from styleforge.tools.weather.schemas import ResolvedLocation, WeatherFacts

from tests.helpers import make_item


class _FakeSession:
    async def list_tools(self) -> Any:
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="echo_json",
                    description="Return deterministic JSON.",
                    inputSchema={"type": "object"},
                )
            ]
        )

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> Any:
        return SimpleNamespace(
            content=[SimpleNamespace(text=json.dumps({"tool": tool, "ok": True}))],
            isError=False,
        )


class _FakeMcpClient(McpClient):
    @asynccontextmanager
    async def _session(self):
        yield _FakeSession()


def test_mcp_client_discovers_calls_and_records_private_safe_trace() -> None:
    mcp_runtime.reset_for_tests()
    client = _FakeMcpClient(
        McpServerConfig(
            name="fake-public-service",
            transport="stdio",
            command="fake-command",
            timeout=2,
        )
    )
    secret_argument = "private-location-never-retained"
    with capture_mcp_traces() as traces:
        response = client.call_tool_sync(
            "echo_json",
            {"location": secret_argument},
        )

    assert response.structured == {"tool": "echo_json", "ok": True}
    assert len(traces) == 1
    assert traces[0]["server"] == "fake-public-service"
    assert traces[0]["tool"] == "echo_json"
    assert traces[0]["success"] is True
    assert secret_argument not in json.dumps(traces)
    snapshot = mcp_runtime.snapshot()
    assert snapshot["successful_calls"] == 1
    assert snapshot["servers"][0]["tools"] == ["echo_json"]


def test_mcp_client_rejects_unknown_tool_and_records_stable_error() -> None:
    mcp_runtime.reset_for_tests()
    client = _FakeMcpClient(
        McpServerConfig(
            name="fake-public-service",
            transport="stdio",
            command="fake-command",
            timeout=2,
        )
    )

    with pytest.raises(McpClientError) as exc_info:
        client.call_tool_sync("missing", {})

    assert exc_info.value.code == "MCP_TOOL_NOT_FOUND"
    trace = mcp_runtime.snapshot()["recent_calls"][0]
    assert trace["success"] is False
    assert trace["error_code"] == "MCP_TOOL_NOT_FOUND"


class _StaticToolClient:
    def __init__(self, response: McpToolResponse | None = None) -> None:
        self.response = response

    def call_tool_sync(self, tool: str, arguments: dict[str, Any]) -> McpToolResponse:
        if self.response is None:
            raise McpClientError("unavailable", code="MCP_TRANSPORT_ERROR")
        return self.response


def test_fetch_transport_enforces_https_host_allowlist_and_extracts_json() -> None:
    response = McpToolResponse(
        server="official-fetch",
        tool="fetch",
        text='Fetched content follows:\n{"latitude": 31.23, "ok": true}',
        structured=None,
        is_error=False,
    )
    transport = McpFetchJsonTransport(
        _StaticToolClient(response),  # type: ignore[arg-type]
        allowed_hosts=("api.open-meteo.com",),
    )

    assert transport("https://api.open-meteo.com/v1/forecast", 5)["ok"] is True
    with pytest.raises(McpClientError) as exc_info:
        transport("http://api.open-meteo.com/v1/forecast", 5)
    assert exc_info.value.code == "MCP_URL_NOT_ALLOWED"
    with pytest.raises(McpClientError):
        transport("https://example.com/private", 5)


class _DirectWeather:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def resolve_location(self, query: str) -> ResolvedLocation:
        if self.fail:
            raise OSError("offline")
        return ResolvedLocation(
            name="上海",
            country="中国",
            latitude=31.23,
            longitude=121.47,
            timezone="Asia/Shanghai",
        )

    def reverse_geocode(self, latitude: float, longitude: float) -> ResolvedLocation:
        return self.resolve_location("上海")

    def forecast(self, location: ResolvedLocation, target_date: date) -> WeatherFacts:
        return self.forecast_range(location, target_date, target_date)

    def forecast_range(
        self,
        location: ResolvedLocation,
        start_date: date,
        end_date: date,
        **_kwargs: Any,
    ) -> WeatherFacts:
        if self.fail:
            raise OSError("offline")
        return WeatherFacts(
            status="available",
            requested_location=location.display_name,
            requested_date=start_date.isoformat(),
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        )


def _mcp_weather(*, direct_fail: bool) -> McpOpenMeteoProvider:
    return McpOpenMeteoProvider(
        fetch_client=_StaticToolClient(),  # type: ignore[arg-type]
        time_client=_StaticToolClient(),  # type: ignore[arg-type]
        timeout=1,
        allowed_hosts=("api.open-meteo.com", "geocoding-api.open-meteo.com"),
        direct_provider=_DirectWeather(fail=direct_fail),  # type: ignore[arg-type]
    )


def test_mcp_weather_uses_direct_fallback_when_mcp_is_unavailable() -> None:
    provider = _mcp_weather(direct_fail=False)
    location = provider.resolve_location("上海")
    assert location is not None

    facts = provider.forecast_range(location, date(2026, 8, 30), date(2026, 8, 31))

    assert facts.status == "available"
    assert facts.source == "open-meteo-direct-fallback"


def test_mcp_weather_returns_structured_unavailable_when_all_paths_fail() -> None:
    provider = _mcp_weather(direct_fail=True)
    location = ResolvedLocation(
        name="上海",
        latitude=31.23,
        longitude=121.47,
        timezone="Asia/Shanghai",
    )

    facts = provider.forecast_range(location, date(2026, 8, 30), date(2026, 8, 31))

    assert facts.status == "unavailable"
    assert facts.error_code == "weather_provider_unavailable"
    assert facts.source == "mcp-fetch+direct-fallback-failed"


class _CountingWeather(_DirectWeather):
    def __init__(self) -> None:
        super().__init__()
        self.resolve_calls = 0
        self.forecast_calls = 0

    def resolve_location(self, query: str) -> ResolvedLocation:
        self.resolve_calls += 1
        return super().resolve_location(query)

    def forecast_range(
        self,
        location: ResolvedLocation,
        start_date: date,
        end_date: date,
        **kwargs: Any,
    ) -> WeatherFacts:
        self.forecast_calls += 1
        return super().forecast_range(location, start_date, end_date, **kwargs)


def test_environment_reuses_same_weather_fact_within_one_task() -> None:
    provider = _CountingWeather()
    environment = Environment.__new__(Environment)
    environment.weather_provider = provider
    environment.facts = SimpleNamespace(weather={})
    environment.mcp_call_traces = []
    environment._weather_cache = {}
    environment.last_weather_facts = None

    first = environment.get_weather(" 上海 ", "2026-08-31")
    second = environment.get_weather("上海", "2026-08-31")

    assert first == second
    assert first is not second
    assert provider.resolve_calls == 1
    assert provider.forecast_calls == 1


def test_styleforge_mcp_exposes_flat_schemas_and_annotations() -> None:
    tools = asyncio.run(styleforge_mcp.list_tools())
    by_name = {tool.name: tool for tool in tools}

    assert set(by_name) == set(STYLEFORGE_MCP_TOOL_NAMES)
    summary_schema = by_name["styleforge_get_wardrobe_summary"].inputSchema
    assert summary_schema["required"] == ["user_id"]
    assert "params" not in summary_schema["properties"]
    execute_schema = by_name["styleforge_execute_task"].inputSchema
    assert set(execute_schema["required"]) == {"user_id", "request"}
    assert by_name["styleforge_get_wardrobe_summary"].annotations.readOnlyHint is True
    assert by_name["styleforge_execute_task"].annotations.readOnlyHint is False


def test_styleforge_stdio_mcp_protocol_reads_tenant_scoped_wardrobe(db_dsn: str) -> None:
    initialize_database(db_dsn)
    items = [
        make_item("mcp-shirt", "top", "White shirt", "white"),
        make_item("mcp-pants", "pants", "Black trousers", "black"),
    ]
    with database_session(db_dsn) as connection:
        upsert_items(connection, items, "mcp-test")
        add_items(connection, "mcp-user", [item.item_id for item in items])

    child_env = dict(os.environ)
    child_env.update(
        {
            "STYLEFORGE_DATABASE_DSN": db_dsn,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
    )
    client = McpClient(
        McpServerConfig(
            name="styleforge-stdio-test",
            transport="stdio",
            command=os.sys.executable,
            args=("-m", "styleforge.mcp_server"),
            env=child_env,
            cwd=Path("apps/api").resolve(),
            timeout=30,
        )
    )

    discovered = {tool["name"] for tool in asyncio.run(client.list_tools())}
    response = client.call_tool_sync(
        "styleforge_get_wardrobe_summary",
        {"user_id": "mcp-user"},
    )

    assert discovered == set(STYLEFORGE_MCP_TOOL_NAMES)
    assert response.structured["user_id"] == "mcp-user"
    assert response.structured["item_count"] == 2
    assert response.structured["slot_counts"] == {"bottom": 1, "top": 1}
