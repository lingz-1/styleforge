"""Safe MCP call traces and process-local runtime status.

Tool arguments and full upstream payloads are deliberately excluded. The
task-run diagnostics keep protocol/tool timing and outcome facts while user
locations and prompts remain private.
"""

from __future__ import annotations

from collections import Counter, deque
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Iterator
import uuid

from styleforge.common.observability import observability


_trace_collector: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "styleforge_mcp_trace_collector", default=None
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@contextmanager
def capture_mcp_traces() -> Iterator[list[dict[str, Any]]]:
    """Capture MCP calls made in the current request/task context."""
    traces: list[dict[str, Any]] = []
    token = _trace_collector.set(traces)
    try:
        yield traces
    finally:
        _trace_collector.reset(token)


class McpRuntimeRegistry:
    """Bounded, thread-safe status for configured MCP servers and calls."""

    def __init__(self, *, recent_limit: int = 30) -> None:
        self._lock = Lock()
        self._servers: dict[str, dict[str, Any]] = {}
        self._recent: deque[dict[str, Any]] = deque(maxlen=recent_limit)
        self._counts = Counter[str]()

    def register_server(
        self,
        *,
        name: str,
        transport: str,
        endpoint: str,
    ) -> None:
        with self._lock:
            current = self._servers.setdefault(name, {})
            current.update(
                {
                    "name": name,
                    "transport": transport,
                    "endpoint": endpoint,
                    "configured": True,
                    "tools": list(current.get("tools") or []),
                    "calls": int(current.get("calls") or 0),
                    "successful_calls": int(current.get("successful_calls") or 0),
                    "failed_calls": int(current.get("failed_calls") or 0),
                    "last_called_at": current.get("last_called_at", ""),
                    "last_error_code": current.get("last_error_code", ""),
                }
            )

    def discovered_tools(self, server: str, tools: list[str]) -> None:
        with self._lock:
            current = self._servers.setdefault(server, {"name": server})
            current["tools"] = sorted(dict.fromkeys(str(tool) for tool in tools))

    def record_call(
        self,
        *,
        server: str,
        tool: str,
        transport: str,
        success: bool,
        duration_ms: float,
        error_code: str = "",
        result_chars: int = 0,
    ) -> dict[str, Any]:
        trace = {
            "call_id": uuid.uuid4().hex,
            "server": str(server)[:80],
            "tool": str(tool)[:100],
            "transport": str(transport)[:32],
            "success": bool(success),
            "duration_ms": round(max(0.0, float(duration_ms)), 2),
            "error_code": str(error_code)[:80],
            "result_chars": max(0, int(result_chars)),
            "called_at": _now(),
        }
        with self._lock:
            server_state = self._servers.setdefault(server, {"name": server})
            server_state["calls"] = int(server_state.get("calls") or 0) + 1
            counter_key = "successful_calls" if success else "failed_calls"
            server_state[counter_key] = int(server_state.get(counter_key) or 0) + 1
            server_state["last_called_at"] = trace["called_at"]
            server_state["last_error_code"] = "" if success else trace["error_code"]
            self._counts["calls"] += 1
            self._counts["successful_calls" if success else "failed_calls"] += 1
            self._recent.append(trace)
        collector = _trace_collector.get()
        if collector is not None:
            collector.append(dict(trace))
        observability.record_operation(
            "mcp_call",
            success=success,
            duration_ms=duration_ms,
            retryable=not success,
            degraded=not success,
            error_code=(error_code or "MCP_CALL_FAILED") if not success else "",
            component="mcp_client",
            server=server,
            tool=tool,
            transport=transport,
        )
        return trace

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            servers = [dict(value) for _, value in sorted(self._servers.items())]
            recent = list(self._recent)
            counts = self._counts.copy()
        return {
            "enabled": bool(servers),
            "servers": servers,
            "calls": int(counts["calls"]),
            "successful_calls": int(counts["successful_calls"]),
            "failed_calls": int(counts["failed_calls"]),
            "recent_calls": recent,
        }

    def reset_for_tests(self) -> None:
        with self._lock:
            self._servers.clear()
            self._recent.clear()
            self._counts.clear()


mcp_runtime = McpRuntimeRegistry()
