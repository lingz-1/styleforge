"""Process-local structured events and low-cardinality runtime metrics.

This is deliberately dependency-free. It provides immediate diagnostics for a
local deployment while keeping user prompts, tool arguments and exception
messages out of both the public health snapshot and retained event records.
"""

from __future__ import annotations

from collections import Counter, deque
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
from threading import Lock
from typing import Any, Iterator


logger = logging.getLogger("styleforge.observability")

_request_id: ContextVar[str] = ContextVar("styleforge_request_id", default="")
_run_id: ContextVar[str] = ContextVar("styleforge_run_id", default="")


def current_observability_context() -> dict[str, str]:
    """Return correlation identifiers bound to the current execution context."""
    return {"request_id": _request_id.get(), "run_id": _run_id.get()}


@contextmanager
def observability_context(
    *,
    request_id: str | None = None,
    run_id: str | None = None,
) -> Iterator[None]:
    """Temporarily bind request/task correlation identifiers."""
    tokens: list[tuple[ContextVar[str], Any]] = []
    if request_id is not None:
        tokens.append((_request_id, _request_id.set(request_id)))
    if run_id is not None:
        tokens.append((_run_id, _run_id.set(run_id)))
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


def _safe_value(value: Any) -> str | int | float | bool:
    if isinstance(value, bool | int | float):
        return value
    return str(value)[:160]


def emit_structured_event(event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    """Emit one JSON log line containing only explicitly supplied safe fields."""
    context = current_observability_context()
    payload: dict[str, Any] = {
        "event": str(event)[:80],
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
    }
    for key, value in {**context, **fields}.items():
        if value not in (None, ""):
            payload[str(key)[:64]] = _safe_value(value)
    logger.log(level, json.dumps(payload, ensure_ascii=False, sort_keys=True))


class ObservabilityRegistry:
    """Thread-safe process-lifetime counters and bounded safe error events."""

    def __init__(self, *, recent_error_limit: int = 100) -> None:
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._lock = Lock()
        self._requests = Counter[str]()
        self._operations: dict[str, Counter[str]] = {}
        self._errors_by_code = Counter[str]()
        self._errors_by_component = Counter[str]()
        self._recent_errors: deque[dict[str, Any]] = deque(maxlen=recent_error_limit)

    def request_started(self) -> None:
        with self._lock:
            self._requests["in_flight"] += 1

    def request_finished(self, *, status_code: int, duration_ms: float) -> None:
        with self._lock:
            self._requests["in_flight"] = max(0, self._requests["in_flight"] - 1)
            self._requests["total"] += 1
            if status_code >= 400:
                self._requests["failed"] += 1
            self._requests["duration_ms_total"] += max(0.0, duration_ms)
            self._requests["duration_ms_max"] = max(
                self._requests["duration_ms_max"], max(0.0, duration_ms)
            )

    def record_operation(
        self,
        operation: str,
        *,
        success: bool,
        duration_ms: float = 0.0,
        retryable: bool = False,
        retries: int = 0,
        degraded: bool = False,
        error_code: str = "",
        component: str = "",
        **fields: Any,
    ) -> None:
        name = str(operation)[:64]
        with self._lock:
            counter = self._operations.setdefault(name, Counter())
            counter["total"] += 1
            if not success:
                counter["failed"] += 1
            if retryable:
                counter["retryable_failures"] += 1
            counter["retries"] += max(0, int(retries))
            if degraded:
                counter["degraded"] += 1
            counter["duration_ms_total"] += max(0.0, duration_ms)
            counter["duration_ms_max"] = max(
                counter["duration_ms_max"], max(0.0, duration_ms)
            )
        if not success:
            self.record_error(
                code=error_code or "UNCLASSIFIED_ERROR",
                component=component or name,
                retryable=retryable,
                **fields,
            )

    def record_error(
        self,
        *,
        code: str,
        component: str,
        retryable: bool,
        **fields: Any,
    ) -> None:
        context = current_observability_context()
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "code": str(code)[:80],
            "component": str(component)[:80],
            "retryable": bool(retryable),
            **{
                key: _safe_value(value)
                for key, value in {**context, **fields}.items()
                if value not in (None, "")
            },
        }
        with self._lock:
            self._errors_by_code[event["code"]] += 1
            self._errors_by_component[event["component"]] += 1
            self._recent_errors.append(event)
        emit_structured_event("error", level=logging.WARNING, **event)

    @staticmethod
    def _duration(counter: Counter[str]) -> dict[str, float]:
        total = int(counter["total"])
        return {
            "total": round(float(counter["duration_ms_total"]), 2),
            "average": round(float(counter["duration_ms_total"]) / total, 2)
            if total
            else 0.0,
            "maximum": round(float(counter["duration_ms_max"]), 2),
        }

    def snapshot(self) -> dict[str, Any]:
        """Public low-cardinality metrics; contains no retained event details."""
        with self._lock:
            requests = self._requests.copy()
            operations = {name: counter.copy() for name, counter in self._operations.items()}
            errors_by_code = dict(sorted(self._errors_by_code.items()))
            errors_by_component = dict(sorted(self._errors_by_component.items()))
        return {
            "scope": "process",
            "started_at": self.started_at,
            "requests": {
                "in_flight": int(requests["in_flight"]),
                "total": int(requests["total"]),
                "failed": int(requests["failed"]),
                "duration_ms": self._duration(requests),
            },
            "operations": {
                name: {
                    "total": int(counter["total"]),
                    "failed": int(counter["failed"]),
                    "retryable_failures": int(counter["retryable_failures"]),
                    "retries": int(counter["retries"]),
                    "degraded": int(counter["degraded"]),
                    "duration_ms": self._duration(counter),
                }
                for name, counter in sorted(operations.items())
            },
            "errors": {
                "total": sum(errors_by_code.values()),
                "by_code": errors_by_code,
                "by_component": errors_by_component,
            },
        }

    def recent_errors(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Internal/test accessor; recent errors are never exposed by health."""
        safe_limit = max(0, min(int(limit), 100))
        with self._lock:
            return list(self._recent_errors)[-safe_limit:] if safe_limit else []

    def render_prometheus(self) -> str:
        """Render a Prometheus text exposition without optional dependencies."""
        snapshot = self.snapshot()
        requests = snapshot["requests"]
        lines = [
            "# HELP styleforge_http_requests_total Completed HTTP requests.",
            "# TYPE styleforge_http_requests_total counter",
            f"styleforge_http_requests_total {requests['total']}",
            "# HELP styleforge_http_request_failures_total Completed HTTP requests with a 4xx or 5xx status.",
            "# TYPE styleforge_http_request_failures_total counter",
            f"styleforge_http_request_failures_total {requests['failed']}",
            "# HELP styleforge_http_requests_in_flight HTTP requests currently being processed.",
            "# TYPE styleforge_http_requests_in_flight gauge",
            f"styleforge_http_requests_in_flight {requests['in_flight']}",
            "# HELP styleforge_http_request_duration_milliseconds_sum Cumulative HTTP request duration in milliseconds.",
            "# TYPE styleforge_http_request_duration_milliseconds_sum counter",
            (
                "styleforge_http_request_duration_milliseconds_sum "
                f"{requests['duration_ms']['total']}"
            ),
            "# HELP styleforge_http_request_duration_milliseconds_max Maximum observed HTTP request duration in milliseconds.",
            "# TYPE styleforge_http_request_duration_milliseconds_max gauge",
            (
                "styleforge_http_request_duration_milliseconds_max "
                f"{requests['duration_ms']['maximum']}"
            ),
        ]

        operation_metrics = (
            ("calls", "total", "StyleForge operation calls.", "counter"),
            ("failures", "failed", "Failed StyleForge operations.", "counter"),
            (
                "retryable_failures",
                "retryable_failures",
                "Retryable StyleForge operation failures.",
                "counter",
            ),
            ("retries", "retries", "StyleForge operation retries.", "counter"),
            ("degraded", "degraded", "Degraded StyleForge operation results.", "counter"),
        )
        operations = snapshot["operations"]
        for suffix, key, help_text, metric_type in operation_metrics:
            metric_name = f"styleforge_operation_{suffix}_total"
            lines.extend(
                [
                    f"# HELP {metric_name} {help_text}",
                    f"# TYPE {metric_name} {metric_type}",
                ]
            )
            for operation, values in operations.items():
                lines.append(
                    f'{metric_name}{{operation="{_prometheus_escape(operation)}"}} '
                    f"{values[key]}"
                )

        for suffix, duration_key, help_text, metric_type in (
            (
                "sum",
                "total",
                "Cumulative StyleForge operation duration in milliseconds.",
                "counter",
            ),
            (
                "max",
                "maximum",
                "Maximum observed StyleForge operation duration in milliseconds.",
                "gauge",
            ),
        ):
            metric_name = f"styleforge_operation_duration_milliseconds_{suffix}"
            lines.extend(
                [
                    f"# HELP {metric_name} {help_text}",
                    f"# TYPE {metric_name} {metric_type}",
                ]
            )
            for operation, values in operations.items():
                lines.append(
                    f'{metric_name}{{operation="{_prometheus_escape(operation)}"}} '
                    f"{values['duration_ms'][duration_key]}"
                )

        for dimension, values in (
            ("code", snapshot["errors"]["by_code"]),
            ("component", snapshot["errors"]["by_component"]),
        ):
            metric_name = f"styleforge_errors_by_{dimension}_total"
            lines.extend(
                [
                    f"# HELP {metric_name} StyleForge errors grouped by {dimension}.",
                    f"# TYPE {metric_name} counter",
                ]
            )
            for label, count in values.items():
                lines.append(
                    f'{metric_name}{{{dimension}="{_prometheus_escape(label)}"}} {count}'
                )

        return "\n".join(lines) + "\n"

    def reset_for_tests(self) -> None:
        with self._lock:
            self._requests.clear()
            self._operations.clear()
            self._errors_by_code.clear()
            self._errors_by_component.clear()
            self._recent_errors.clear()


observability = ObservabilityRegistry()


def _prometheus_escape(value: str) -> str:
    """Escape a Prometheus label value."""
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
