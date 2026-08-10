"""Typed registry for external factual tools used by orchestration nodes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class ToolRegistration:
    input_model: type[BaseModel]
    handler: Callable[[Any], Any]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolRegistration] = {}

    def register(
        self,
        name: str,
        *,
        input_model: type[BaseModel],
        handler: Callable[[Any], Any],
    ) -> None:
        if not name.strip():
            raise ValueError("tool name must not be empty")
        if name in self._tools:
            raise ValueError(f"tool already registered: {name}")
        self._tools[name] = ToolRegistration(input_model=input_model, handler=handler)

    def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        try:
            registration = self._tools[name]
        except KeyError as error:
            raise KeyError(f"tool is not registered: {name}") from error
        validated = registration.input_model.model_validate(arguments)
        return registration.handler(validated)

    def contains(self, name: str) -> bool:
        return name in self._tools
