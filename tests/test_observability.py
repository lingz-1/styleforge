"""Structured observability and dependency fault-injection tests."""

from __future__ import annotations

import importlib
from pathlib import Path
import sys

from fastapi.testclient import TestClient
from pydantic import BaseModel
import pytest

from styleforge.agentic.agentic_contract import StylistDecision
from styleforge.agentic.runtime.agent_runtime import AgentRuntime
from styleforge.agentic.runtime.capability_registry import CapabilityRegistry, ToolCapability
from styleforge.agentic.runtime.tool_runtime import STATUS_ERROR, ToolRuntime
from styleforge.common.observability import (
    ObservabilityRegistry,
    observability,
    observability_context,
)
from styleforge.repositories import database
from styleforge.repositories.database import initialize_database
from styleforge.models.task import TaskExecutionInput
from styleforge.workflow.task_workflow import MultiTaskWorkflow
from tests.llm.fake_llm import FakeLlm


def _import_api(db_dsn: str, monkeypatch):
    monkeypatch.setenv("STYLEFORGE_DATABASE_DSN", db_dsn)
    sys.modules.pop("styleforge.api", None)
    return importlib.import_module("styleforge.api")


def test_registry_keeps_correlations_internal_and_health_snapshot_safe() -> None:
    registry = ObservabilityRegistry()
    with observability_context(request_id="request-1", run_id="run-1"):
        registry.record_operation(
            "tool_call",
            success=False,
            retryable=True,
            retries=2,
            degraded=True,
            error_code="TOOL_TIMEOUT",
            component="tool_runtime",
            tool="weather",
        )

    snapshot = registry.snapshot()
    recent = registry.recent_errors()
    assert snapshot["operations"]["tool_call"]["failed"] == 1
    assert snapshot["operations"]["tool_call"]["retries"] == 2
    assert snapshot["operations"]["tool_call"]["degraded"] == 1
    assert snapshot["errors"]["by_code"] == {"TOOL_TIMEOUT": 1}
    assert "request-1" not in str(snapshot)
    assert recent[0]["request_id"] == "request-1"
    assert recent[0]["run_id"] == "run-1"
    assert "arguments" not in recent[0]


def test_registry_renders_prometheus_metrics_without_correlations() -> None:
    registry = ObservabilityRegistry()
    registry.request_started()
    registry.request_finished(status_code=503, duration_ms=12.5)
    with observability_context(request_id="private-request", run_id="private-run"):
        registry.record_operation(
            'tool_"call',
            success=False,
            duration_ms=8.25,
            retryable=True,
            error_code="TOOL_TIMEOUT",
            component="tool_runtime",
        )

    payload = registry.render_prometheus()

    assert "styleforge_http_requests_total 1" in payload
    assert "styleforge_http_request_failures_total 1" in payload
    assert 'styleforge_operation_calls_total{operation="tool_\\\"call"} 1' in payload
    assert 'styleforge_errors_by_code_total{code="TOOL_TIMEOUT"} 1' in payload
    assert "private-request" not in payload
    assert "private-run" not in payload


@pytest.mark.parametrize(
    ("injected", "status_code", "error_code"),
    [
        (TimeoutError("slow upstream"), 504, "UPSTREAM_TIMEOUT"),
        (ConnectionError("offline upstream"), 503, "DEPENDENCY_UNAVAILABLE"),
    ],
)
def test_http_dependency_faults_are_safe_and_counted(
    db_dsn: str,
    monkeypatch,
    injected: Exception,
    status_code: int,
    error_code: str,
) -> None:
    api = _import_api(db_dsn, monkeypatch)
    observability.reset_for_tests()

    def explode():
        raise injected

    monkeypatch.setattr(api, "get_task_graph", explode)
    with TestClient(api.app, raise_server_exceptions=False) as client:
        response = client.post(
            "/tasks/route",
            headers={"X-Request-ID": "fault-test"},
            json={"user_id": "u", "request": "通勤穿什么"},
        )
        health = client.get("/health").json()

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == error_code
    assert response.json()["error"]["retryable"] is True
    assert str(injected) not in response.text
    metrics = health["observability"]
    assert metrics["requests"]["failed"] == 1
    assert metrics["errors"]["by_code"][error_code] == 1


def test_metrics_endpoint_uses_prometheus_text_format(db_dsn: str, monkeypatch) -> None:
    api = _import_api(db_dsn, monkeypatch)
    observability.reset_for_tests()

    with TestClient(api.app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "text/plain; version=0.0.4; charset=utf-8"
    )
    assert "# TYPE styleforge_http_requests_total counter" in response.text
    assert "styleforge_http_requests_in_flight 1" in response.text


def test_tool_timeout_increments_retryable_tool_metrics() -> None:
    class Input(BaseModel):
        query: str

    def timeout(_input: Input, _context):
        raise TimeoutError("secret tool detail")

    registry = CapabilityRegistry()
    registry.register_tool(
        ToolCapability(
            name="slow_tool",
            description="test",
            input_model=Input,
            handler=timeout,
        )
    )
    observability.reset_for_tests()

    result = ToolRuntime(registry).execute(
        "slow_tool",
        {"query": "private argument"},
        state={"run_id": "run-tool"},
    )

    metrics = observability.snapshot()
    assert result.status == STATUS_ERROR
    assert metrics["operations"]["tool_call"]["failed"] == 1
    assert metrics["operations"]["tool_call"]["retryable_failures"] == 1
    assert metrics["errors"]["by_code"]["TOOL_TIMEOUT"] == 1
    assert "private argument" not in str(metrics)


def test_llm_outage_is_normalized_at_agent_boundary_and_counted() -> None:
    observability.reset_for_tests()
    runtime = AgentRuntime(
        llm=FakeLlm([], fail_after=0),
        registry=CapabilityRegistry(),
        instructions_root=(
            Path(__file__).parents[1]
            / "apps"
            / "api"
            / "styleforge"
            / "agentic"
            / "instructions"
        ),
    )

    result = runtime.call(
        "stylist",
        {"run_id": "run-llm", "request": "test"},
        decision_model=StylistDecision,
    )

    metrics = observability.snapshot()
    assert result.error_code == "LLM_UNAVAILABLE"
    assert result.retryable is True
    assert metrics["operations"]["llm_call"]["failed"] == 1
    assert metrics["operations"]["agent_call"]["failed"] == 1
    assert metrics["errors"]["by_code"]["LLM_UNAVAILABLE"] == 2


def test_database_transaction_fault_rolls_back_and_is_counted(monkeypatch) -> None:
    class FakeConnection:
        committed = False
        rolled_back = False
        closed = False

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            self.closed = True

    connection = FakeConnection()
    monkeypatch.setattr(database, "connect", lambda _dsn: connection)
    observability.reset_for_tests()

    with pytest.raises(RuntimeError, match="business failure"):
        with database.database_session("test-dsn"):
            raise RuntimeError("business failure")

    metrics = observability.snapshot()
    assert connection.committed is False
    assert connection.rolled_back is True
    assert connection.closed is True
    assert metrics["operations"]["database_session"]["failed"] == 1
    assert metrics["errors"]["by_code"]["TRANSACTION_ABORTED"] == 1


def test_task_run_metrics_correlate_success_without_exposing_run_id(db_dsn: str) -> None:
    initialize_database(db_dsn)
    workflow = MultiTaskWorkflow(
        database_path=db_dsn,
        knowledge_root=Path("knowledge"),
        llm_client=None,
    )
    observability.reset_for_tests()

    payload = workflow.execute(
        TaskExecutionInput(user_id="metrics-user", request="推荐一套日常穿搭")
    )

    metrics = observability.snapshot()
    assert metrics["operations"]["task_run"]["total"] == 1
    assert metrics["operations"]["task_run"]["failed"] == 0
    assert metrics["operations"]["database_session"]["total"] >= 1
    assert payload["run_id"] not in str(metrics)
