"""Transport-neutral MCP client with stdio and Streamable HTTP support."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from contextvars import copy_context
from dataclasses import dataclass, field
from datetime import timedelta
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

import anyio
import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from styleforge.integrations.mcp.telemetry import mcp_runtime


class McpClientError(RuntimeError):
    """Safe MCP boundary error carrying a stable diagnostic code."""

    def __init__(self, message: str, *, code: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class McpServerConfig:
    name: str
    transport: Literal["stdio", "streamable_http"]
    command: str = ""
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    cwd: Path | None = None
    url: str = ""
    timeout: float = 20.0

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("MCP server name must not be empty")
        if self.transport == "stdio" and not self.command.strip():
            raise ValueError("stdio MCP server requires a command")
        if self.transport == "streamable_http" and not self.url.strip():
            raise ValueError("Streamable HTTP MCP server requires a URL")
        if self.timeout <= 0:
            raise ValueError("MCP timeout must be positive")

    @property
    def endpoint_label(self) -> str:
        if self.transport == "streamable_http":
            return self.url
        module = " ".join(self.args[:2]).strip()
        return module or Path(self.command).name


@dataclass(frozen=True, slots=True)
class McpToolResponse:
    server: str
    tool: str
    text: str
    structured: Any | None
    is_error: bool


def _result_payload(result: Any) -> tuple[str, Any | None, bool]:
    dumped = result.model_dump(by_alias=True) if hasattr(result, "model_dump") else {}
    structured = dumped.get("structuredContent") or dumped.get("structured_content")
    texts: list[str] = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text is not None:
            texts.append(str(text))
    combined = "\n".join(texts).strip()
    if structured is None and combined:
        try:
            structured = json.loads(combined)
        except json.JSONDecodeError:
            structured = None
    return combined, structured, bool(getattr(result, "isError", False) or dumped.get("isError"))


class McpClient:
    """Open a short-lived standards-compliant MCP session per operation.

    Short-lived sessions avoid sharing event loops across FastAPI worker
    threads and keep failure recovery deterministic. Official Time/Fetch
    processes are lightweight and calls are bounded by ``timeout``.
    """

    def __init__(self, config: McpServerConfig) -> None:
        self.config = config
        mcp_runtime.register_server(
            name=config.name,
            transport=config.transport,
            endpoint=config.endpoint_label,
        )

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[ClientSession]:
        timeout = timedelta(seconds=self.config.timeout)
        if self.config.transport == "stdio":
            parameters = StdioServerParameters(
                command=self.config.command,
                args=list(self.config.args),
                env=dict(self.config.env) or None,
                cwd=self.config.cwd,
                encoding="utf-8",
                encoding_error_handler="replace",
            )
            async with stdio_client(parameters) as (read_stream, write_stream):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timeout,
                ) as session:
                    await session.initialize()
                    yield session
            return
        async with httpx.AsyncClient(timeout=self.config.timeout) as http_client:
            async with streamable_http_client(
                self.config.url,
                http_client=http_client,
            ) as (read_stream, write_stream, _session_id):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timeout,
                ) as session:
                    await session.initialize()
                    yield session

    async def list_tools(self) -> list[dict[str, Any]]:
        try:
            with anyio.fail_after(self.config.timeout):
                async with self._session() as session:
                    response = await session.list_tools()
        except TimeoutError as error:
            raise McpClientError(
                f"MCP server {self.config.name} timed out while listing tools",
                code="MCP_TIMEOUT",
            ) from error
        except Exception as error:
            raise McpClientError(
                f"MCP server {self.config.name} is unavailable ({type(error).__name__})",
                code="MCP_TRANSPORT_ERROR",
            ) from error
        tools = [
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.inputSchema,
            }
            for tool in response.tools
        ]
        mcp_runtime.discovered_tools(
            self.config.name, [str(tool["name"]) for tool in tools]
        )
        return tools

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> McpToolResponse:
        started = perf_counter()
        error_code = ""
        result_chars = 0
        success = False
        try:
            with anyio.fail_after(self.config.timeout):
                async with self._session() as session:
                    listed = await session.list_tools()
                    tool_names = [entry.name for entry in listed.tools]
                    mcp_runtime.discovered_tools(self.config.name, tool_names)
                    if tool not in tool_names:
                        raise McpClientError(
                            f"MCP tool {tool} is not exposed by {self.config.name}",
                            code="MCP_TOOL_NOT_FOUND",
                            retryable=False,
                        )
                    result = await session.call_tool(tool, arguments)
            text, structured, is_error = _result_payload(result)
            result_chars = len(text)
            if is_error:
                raise McpClientError(
                    f"MCP tool {self.config.name}/{tool} returned an error",
                    code="MCP_TOOL_ERROR",
                )
            success = True
            return McpToolResponse(
                server=self.config.name,
                tool=tool,
                text=text,
                structured=structured,
                is_error=False,
            )
        except McpClientError as error:
            error_code = error.code
            raise
        except TimeoutError as error:
            error_code = "MCP_TIMEOUT"
            raise McpClientError(
                f"MCP tool {self.config.name}/{tool} timed out",
                code=error_code,
            ) from error
        except Exception as error:
            error_code = "MCP_TRANSPORT_ERROR"
            raise McpClientError(
                f"MCP tool {self.config.name}/{tool} failed ({type(error).__name__})",
                code=error_code,
            ) from error
        finally:
            mcp_runtime.record_call(
                server=self.config.name,
                tool=tool,
                transport=self.config.transport,
                success=success,
                duration_ms=(perf_counter() - started) * 1000.0,
                error_code=error_code,
                result_chars=result_chars,
            )

    def call_tool_sync(self, tool: str, arguments: dict[str, Any]) -> McpToolResponse:
        """Call an async MCP transport from the synchronous workflow safely."""

        def factory() -> McpToolResponse:
            return asyncio.run(self.call_tool(tool, arguments))

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return factory()
        context = copy_context()
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="styleforge-mcp") as pool:
            return pool.submit(context.run, factory).result(timeout=self.config.timeout + 2)
