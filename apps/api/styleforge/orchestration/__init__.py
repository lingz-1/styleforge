"""Task-level orchestration for the v3.3 multi-task decision graph."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from styleforge.orchestration.graph import MultiTaskGraph
    from styleforge.orchestration.task_router import TaskRoute, TaskRouter, TaskType

__all__ = ["MultiTaskGraph", "TaskRoute", "TaskRouter", "TaskType"]


def __getattr__(name: str):
    """Keep the executable LangGraph module lazy during package imports."""
    if name == "MultiTaskGraph":
        from styleforge.orchestration.graph import MultiTaskGraph

        return MultiTaskGraph
    if name in {"TaskRoute", "TaskRouter", "TaskType"}:
        from styleforge.orchestration.task_router import TaskRoute, TaskRouter, TaskType

        return {"TaskRoute": TaskRoute, "TaskRouter": TaskRouter, "TaskType": TaskType}[name]
    raise AttributeError(name)
