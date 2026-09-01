"""Open-Meteo weather provider driven by official Time and Fetch MCP servers."""

from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlsplit

from styleforge.core.config import Settings
from styleforge.integrations.mcp.client import McpClient, McpClientError, McpServerConfig
from styleforge.tools.weather.provider import OpenMeteoProvider
from styleforge.tools.weather.schemas import ResolvedLocation, WeatherFacts


def _server_config(
    *,
    name: str,
    module: str,
    url: str,
    timeout: float,
    extra_args: tuple[str, ...] = (),
) -> McpServerConfig:
    if url:
        return McpServerConfig(
            name=name,
            transport="streamable_http",
            url=url,
            timeout=timeout,
        )
    return McpServerConfig(
        name=name,
        transport="stdio",
        command=sys.executable,
        args=("-m", module, *extra_args),
        env={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        cwd=Path.cwd(),
        timeout=timeout,
    )


def _extract_json(text: str) -> dict[str, Any]:
    """Extract the first JSON object from Fetch MCP's explanatory envelope."""
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise McpClientError(
        "Fetch MCP returned content without a JSON object",
        code="MCP_INVALID_RESPONSE",
        retryable=False,
    )


class McpFetchJsonTransport:
    """JSON transport compatible with ``OpenMeteoProvider`` via Fetch MCP."""

    def __init__(self, client: McpClient, *, allowed_hosts: tuple[str, ...]) -> None:
        self.client = client
        self.allowed_hosts = frozenset(host.lower() for host in allowed_hosts)

    def __call__(self, url: str, timeout: float) -> dict[str, Any]:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or not host
            or host not in self.allowed_hosts
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise McpClientError(
                f"Fetch MCP URL host is not allowed: {host or 'missing'}",
                code="MCP_URL_NOT_ALLOWED",
                retryable=False,
            )
        response = self.client.call_tool_sync(
            "fetch",
            {
                "url": url,
                "max_length": 500_000,
                "start_index": 0,
                "raw": True,
            },
        )
        if isinstance(response.structured, dict):
            return response.structured
        return _extract_json(response.text)


class McpOpenMeteoProvider:
    """MCP-first provider with the existing direct adapter as a safe fallback."""

    name = "mcp-fetch+open-meteo"

    def __init__(
        self,
        *,
        fetch_client: McpClient,
        time_client: McpClient,
        timeout: float,
        allowed_hosts: tuple[str, ...],
        direct_provider: OpenMeteoProvider | None = None,
    ) -> None:
        self.fetch_client = fetch_client
        self.time_client = time_client
        self.direct_provider = direct_provider or OpenMeteoProvider(timeout=timeout)
        self.mcp_provider = OpenMeteoProvider(
            timeout=timeout,
            transport=McpFetchJsonTransport(
                fetch_client,
                allowed_hosts=allowed_hosts,
            ),
        )

    def current_date(self, timezone_name: str) -> date:
        """Resolve the target location's calendar date through Time MCP."""
        try:
            response = self.time_client.call_tool_sync(
                "get_current_time", {"timezone": timezone_name or "UTC"}
            )
            payload = response.structured
            if not isinstance(payload, dict):
                payload = _extract_json(response.text)
            return datetime.fromisoformat(str(payload["datetime"])).date()
        except Exception:
            return date.today()

    def resolve_location(self, query: str) -> ResolvedLocation | None:
        try:
            return self.mcp_provider.resolve_location(query)
        except Exception:
            try:
                return self.direct_provider.resolve_location(query)
            except Exception:
                return None

    def reverse_geocode(
        self, latitude: float, longitude: float
    ) -> ResolvedLocation | None:
        try:
            return self.mcp_provider.reverse_geocode(latitude, longitude)
        except Exception:
            try:
                return self.direct_provider.reverse_geocode(latitude, longitude)
            except Exception:
                return None

    def forecast(self, location: ResolvedLocation, target_date: date) -> WeatherFacts:
        try:
            facts = self.mcp_provider.forecast(location, target_date)
            return facts.model_copy(update={"source": self.name})
        except Exception:
            try:
                facts = self.direct_provider.forecast(location, target_date)
                return facts.model_copy(update={"source": "open-meteo-direct-fallback"})
            except Exception as error:
                return WeatherFacts.unavailable(
                    requested_location=location.display_name,
                    requested_date=target_date.isoformat(),
                    error_code="weather_provider_unavailable",
                    error_message=(
                        "MCP Fetch and direct Open-Meteo providers are unavailable "
                        f"({type(error).__name__})"
                    ),
                ).model_copy(update={"source": "mcp-fetch+direct-fallback-failed"})

    def forecast_range(
        self,
        location: ResolvedLocation,
        start_date: date,
        end_date: date,
        *,
        granularity: str = "daily",
        period: str | None = None,
    ) -> WeatherFacts:
        try:
            facts = self.mcp_provider.forecast_range(
                location,
                start_date,
                end_date,
                granularity=granularity,
                period=period,
            )
            return facts.model_copy(update={"source": self.name})
        except Exception:
            try:
                facts = self.direct_provider.forecast_range(
                    location,
                    start_date,
                    end_date,
                    granularity=granularity,
                    period=period,
                )
                return facts.model_copy(update={"source": "open-meteo-direct-fallback"})
            except Exception as error:
                return WeatherFacts.unavailable(
                    requested_location=location.display_name,
                    requested_date=f"{start_date.isoformat()}..{end_date.isoformat()}",
                    error_code="weather_provider_unavailable",
                    error_message=(
                        "MCP Fetch and direct Open-Meteo providers are unavailable "
                        f"({type(error).__name__})"
                    ),
                ).model_copy(update={"source": "mcp-fetch+direct-fallback-failed"})


def build_mcp_weather_provider(settings: Settings) -> McpOpenMeteoProvider:
    """Build official Time/Fetch clients from environment-backed settings."""
    time_config = _server_config(
        name="official-time",
        module="mcp_server_time",
        url=settings.mcp_time_url,
        timeout=settings.mcp_timeout,
    )
    fetch_config = _server_config(
        name="official-fetch",
        module="mcp_server_fetch",
        url=settings.mcp_fetch_url,
        timeout=settings.mcp_timeout,
        extra_args=("--ignore-robots-txt",),
    )
    return McpOpenMeteoProvider(
        fetch_client=McpClient(fetch_config),
        time_client=McpClient(time_config),
        timeout=settings.weather_timeout,
        allowed_hosts=settings.mcp_fetch_allowed_hosts,
        direct_provider=OpenMeteoProvider(timeout=settings.weather_timeout),
    )
