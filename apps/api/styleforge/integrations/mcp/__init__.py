"""Model Context Protocol client integration for external factual services."""

from styleforge.integrations.mcp.client import (
    McpClient,
    McpClientError,
    McpServerConfig,
    McpToolResponse,
)
from styleforge.integrations.mcp.telemetry import (
    capture_mcp_traces,
    mcp_runtime,
)
from styleforge.integrations.mcp.weather import build_mcp_weather_provider

__all__ = [
    "McpClient",
    "McpClientError",
    "McpServerConfig",
    "McpToolResponse",
    "build_mcp_weather_provider",
    "capture_mcp_traces",
    "mcp_runtime",
]
