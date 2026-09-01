"""Shared error contracts for API, workflow, and tool boundaries.

The public contract intentionally keeps FastAPI's historical ``detail`` field
while adding a stable machine-readable ``error`` object. Unexpected exception
text is never returned to clients; the request id links the safe response to
the server-side traceback.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    """Stable error codes shared by every StyleForge client."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    RATE_LIMITED = "RATE_LIMITED"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    VISION_UNAVAILABLE = "VISION_UNAVAILABLE"
    UPSTREAM_INVALID_RESPONSE = "UPSTREAM_INVALID_RESPONSE"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    DATABASE_ERROR = "DATABASE_ERROR"
    AGENT_PROTOCOL_ERROR = "AGENT_PROTOCOL_ERROR"
    AGENT_EXECUTION_FAILED = "AGENT_EXECUTION_FAILED"
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    TOOL_NOT_AUTHORIZED = "TOOL_NOT_AUTHORIZED"
    TOOL_INPUT_INVALID = "TOOL_INPUT_INVALID"
    PROMPT_INJECTION_BLOCKED = "PROMPT_INJECTION_BLOCKED"
    TOOL_PRECONDITION_FAILED = "TOOL_PRECONDITION_FAILED"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
    TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    HTTP_ERROR = "HTTP_ERROR"


@dataclass(slots=True)
class StyleForgeError(Exception):
    """An expected failure with a safe public representation."""

    code: ErrorCode
    message: str
    status_code: int = 500
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)


def error_code_for_status(status_code: int) -> ErrorCode:
    """Map legacy HTTP exceptions into the stable error-code vocabulary."""
    return {
        404: ErrorCode.NOT_FOUND,
        409: ErrorCode.CONFLICT,
        413: ErrorCode.PAYLOAD_TOO_LARGE,
        422: ErrorCode.VALIDATION_ERROR,
        429: ErrorCode.RATE_LIMITED,
        502: ErrorCode.UPSTREAM_INVALID_RESPONSE,
        503: ErrorCode.DEPENDENCY_UNAVAILABLE,
        504: ErrorCode.UPSTREAM_TIMEOUT,
    }.get(status_code, ErrorCode.INTERNAL_ERROR if status_code >= 500 else ErrorCode.HTTP_ERROR)


def is_retryable_status(status_code: int) -> bool:
    """Whether a client may safely retry after backoff."""
    return status_code in {429, 502, 503, 504}


def error_payload(
    *,
    detail: Any,
    code: ErrorCode | str,
    message: str,
    request_id: str,
    retryable: bool,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the backward-compatible public error response."""
    error: dict[str, Any] = {
        "code": code.value if isinstance(code, ErrorCode) else str(code),
        "message": message,
        "request_id": request_id,
        "retryable": retryable,
    }
    if details:
        error["details"] = details
    return {"detail": detail, "error": error}
