"""State contract shared by task routing and execution modes."""

from __future__ import annotations

from typing import Any, TypedDict

from styleforge.orchestration.task_router import TaskRoute, TaskType


class MultiTaskState(TypedDict, total=False):
    user_id: str
    request: str
    current_outfit_id: str
    has_candidate_item: bool
    requested_task_type: TaskType
    precomputed_route: TaskRoute
    route: TaskRoute
    task_type: str
    selected_subgraph: str
    status: str
    required_capabilities: list[str]
    extracted: dict[str, Any]
    trace: list[dict[str, Any]]
