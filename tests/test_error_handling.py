"""Unified error-contract tests across HTTP, Agent, and tool boundaries."""

from __future__ import annotations

import importlib
import sys

from fastapi.testclient import TestClient
from pydantic import BaseModel

from styleforge.agentic.runtime.capability_registry import CapabilityRegistry, ToolCapability
from styleforge.agentic.runtime.tool_runtime import STATUS_ERROR, ToolRuntime
from styleforge.common.errors import ErrorCode, error_code_for_status, error_payload


def _import_api(db_dsn: str, monkeypatch):
    monkeypatch.setenv("STYLEFORGE_DATABASE_DSN", db_dsn)
    sys.modules.pop("styleforge.api", None)
    return importlib.import_module("styleforge.api")


def test_error_payload_keeps_legacy_detail_and_machine_contract() -> None:
    payload = error_payload(
        detail="missing",
        code=ErrorCode.NOT_FOUND,
        message="missing",
        request_id="req-1",
        retryable=False,
    )

    assert payload["detail"] == "missing"
    assert payload["error"] == {
        "code": "NOT_FOUND",
        "message": "missing",
        "request_id": "req-1",
        "retryable": False,
    }
    assert error_code_for_status(503) is ErrorCode.DEPENDENCY_UNAVAILABLE


def test_http_errors_have_request_id_and_stable_code(db_dsn: str, monkeypatch) -> None:
    api = _import_api(db_dsn, monkeypatch)

    with TestClient(api.app) as client:
        response = client.get("/missing", headers={"X-Request-ID": "trace-123"})

    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == "trace-123"
    assert response.json()["detail"] == "Not Found"
    assert response.json()["error"] == {
        "code": "NOT_FOUND",
        "message": "Not Found",
        "request_id": "trace-123",
        "retryable": False,
    }


def test_validation_errors_use_generated_safe_request_id(db_dsn: str, monkeypatch) -> None:
    api = _import_api(db_dsn, monkeypatch)

    with TestClient(api.app) as client:
        response = client.post(
            "/tasks/route",
            headers={"X-Request-ID": "invalid id with spaces"},
            json={"user_id": "", "request": ""},
        )

    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["message"] == "请求参数不合法"
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]
    assert body["error"]["request_id"] != "invalid id with spaces"


def test_unexpected_http_error_hides_internal_exception(db_dsn: str, monkeypatch) -> None:
    api = _import_api(db_dsn, monkeypatch)

    def explode():
        raise RuntimeError("secret database detail")

    monkeypatch.setattr(api, "get_task_graph", explode)
    with TestClient(api.app, raise_server_exceptions=False) as client:
        response = client.post(
            "/tasks/route",
            json={"user_id": "u", "request": "通勤穿什么"},
        )

    body = response.json()
    assert response.status_code == 500
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["retryable"] is False
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]
    assert "secret database detail" not in response.text


def test_tool_timeout_is_normalized_with_retry_metadata() -> None:
    class Input(BaseModel):
        query: str

    def timeout(_input: Input, _context):
        raise TimeoutError("provider timed out")

    registry = CapabilityRegistry()
    registry.register_tool(
        ToolCapability(
            name="slow_tool",
            description="test",
            input_model=Input,
            handler=timeout,
        )
    )
    result = ToolRuntime(registry).execute(
        "slow_tool",
        {"query": "x"},
        state={},
    )

    assert result.status == STATUS_ERROR
    assert result.error_code == "TOOL_TIMEOUT"
    assert result.retryable is True
