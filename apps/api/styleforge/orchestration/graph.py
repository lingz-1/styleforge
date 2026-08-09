"""Route-only graph for the six StyleForge task types.

Task execution belongs exclusively to ``MultiTaskWorkflow``. This graph cannot
accept business handlers or produce task results.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langgraph.graph import END, START, StateGraph

from styleforge.orchestration.state import MultiTaskState
from styleforge.orchestration.task_router import TASK_SUBGRAPHS, TaskRouter, TaskType


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MultiTaskGraph:
    def __init__(self, router: TaskRouter | None = None) -> None:
        self.router = router or TaskRouter()
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(MultiTaskState)
        builder.add_node("task_router", self._route_node)
        for subgraph in TASK_SUBGRAPHS.values():
            builder.add_node(subgraph, self._subgraph_node(subgraph))
        builder.add_edge(START, "task_router")
        builder.add_conditional_edges(
            "task_router",
            self._select_subgraph,
            {subgraph: subgraph for subgraph in TASK_SUBGRAPHS.values()},
        )
        for subgraph in TASK_SUBGRAPHS.values():
            builder.add_edge(subgraph, END)
        return builder.compile()

    def _route_node(self, state: MultiTaskState) -> dict[str, Any]:
        route = state.get("precomputed_route")
        if route is None:
            route = self.router.route(
                state["request"],
                current_outfit_id=state.get("current_outfit_id", ""),
                has_candidate_item=state.get("has_candidate_item", False),
                requested_task_type=state.get("requested_task_type"),
            )
        return {
            "route": route,
            "task_type": route.task_type.value,
            "trace": [
                {
                    "node": "task_router",
                    "timestamp": _now(),
                    "task_type": route.task_type.value,
                    "reason": route.reason,
                }
            ],
        }

    @staticmethod
    def _select_subgraph(state: MultiTaskState) -> str:
        return state["route"].subgraph

    @staticmethod
    def _subgraph_name(state: MultiTaskState) -> str:
        return state["route"].subgraph

    def _subgraph_node(self, subgraph: str):
        def run_subgraph(state: MultiTaskState) -> dict[str, Any]:
            route = state["route"]
            update: dict[str, Any] = {
                "selected_subgraph": subgraph,
                "status": "routed",
                "required_capabilities": list(route.required_capabilities),
                "extracted": dict(route.extracted),
                "trace": [
                    *state.get("trace", []),
                    {
                        "node": subgraph,
                        "timestamp": _now(),
                        "status": "routed",
                    },
                ],
            }
            return update

        return run_subgraph

    def route(
        self,
        *,
        user_id: str,
        request: str,
        current_outfit_id: str = "",
        has_candidate_item: bool = False,
        requested_task_type: TaskType | None = None,
    ) -> dict[str, Any]:
        final = self.graph.invoke(
            {
                "user_id": user_id,
                "request": request,
                "current_outfit_id": current_outfit_id,
                "has_candidate_item": has_candidate_item,
                "requested_task_type": requested_task_type,
            }
        )
        return {
            "user_id": user_id,
            "request": request,
            "task_type": final["task_type"],
            "selected_subgraph": final["selected_subgraph"],
            "status": final["status"],
            "route": final["route"].to_dict(),
            "required_capabilities": final["required_capabilities"],
            "extracted": final.get("extracted", {}),
            "trace": final.get("trace", []),
        }
