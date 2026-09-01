"""StyleForge MCP server for local stdio and Streamable HTTP clients."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from styleforge.core.categories import infer_slot
from styleforge.core.config import Settings
from styleforge.models.task import CandidateItem, TaskExecutionInput
from styleforge.orchestration.task_router import TaskType
from styleforge.repositories.database import database_session
from styleforge.repositories.task_run_repository import get_task_run
from styleforge.repositories.user_preferences_repository import get_evaluation_weights
from styleforge.repositories.wardrobe_repository import list_items


STYLEFORGE_MCP_TOOL_NAMES = (
    "styleforge_get_system_status",
    "styleforge_get_wardrobe_summary",
    "styleforge_search_wardrobe",
    "styleforge_execute_task",
    "styleforge_get_task_result",
)
STYLEFORGE_MCP_RESOURCE_URIS = ("styleforge://capabilities",)
STYLEFORGE_MCP_PROMPT_NAMES = ("styleforge_plan_outfit",)

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_EXECUTE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)


class _StrictInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class WardrobeUserInput(_StrictInput):
    user_id: str = Field(
        min_length=1,
        max_length=128,
        description="StyleForge user identifier, for example demo-user.",
    )


class WardrobeSearchInput(WardrobeUserInput):
    query: str = Field(
        min_length=1,
        max_length=240,
        description="Natural-language garment search, such as 白色通勤衬衫.",
    )
    item_types: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="Optional exact catalog item_type filters.",
    )
    colors: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="Optional case-insensitive color filters.",
    )
    limit: int = Field(default=10, ge=1, le=20)


class ExecuteTaskInput(_StrictInput):
    user_id: str = Field(min_length=1, max_length=128)
    request: str = Field(
        min_length=1,
        max_length=2000,
        description="The user's styling request in natural language.",
    )
    task_type: TaskType | None = Field(
        default=None,
        description="Optional explicit one of the six StyleForge task types.",
    )
    max_results: int = Field(default=3, ge=1, le=5)
    current_outfit_id: str = Field(default="", max_length=128)
    current_item_ids: list[str] = Field(default_factory=list, max_length=12)
    target_slot: str = Field(default="", max_length=32)
    item_id: str = Field(default="", max_length=128)
    selected_item_id: str = Field(default="", max_length=128)
    candidate_item: CandidateItem | None = None
    session_id: str = Field(default="", max_length=128)

    def to_domain(self) -> TaskExecutionInput:
        return TaskExecutionInput(
            user_id=self.user_id,
            request=self.request,
            max_results=self.max_results,
            requested_task_type=self.task_type,
            current_outfit_id=self.current_outfit_id,
            current_item_ids=self.current_item_ids,
            target_slot=self.target_slot,
            item_id=self.item_id,
            selected_item_id=self.selected_item_id,
            candidate_item=self.candidate_item,
            session_id=self.session_id,
        )


class TaskResultInput(WardrobeUserInput):
    run_id: str = Field(min_length=1, max_length=128)


def _settings() -> Settings:
    return Settings.from_env()


def _safe_item(item: Any) -> dict[str, Any]:
    return {
        "item_id": item.item_id,
        "name": item.name,
        "item_type": item.item_type,
        "main_category": item.main_category,
        "slot": infer_slot(item.item_type),
        "color": item.color,
        "description": item.description,
        "gender": item.gender,
        "image_available": item.image_status.value == "available",
        "embedding_ready": item.embedding_status.value == "ready",
        "image_url": f"/items/{item.item_id}/image",
    }


def _keyword_score(item: Any, query: str) -> int:
    haystack = " ".join(
        (
            item.name or "",
            item.item_type or "",
            item.main_category or "",
            item.color or "",
            item.description or "",
        )
    ).lower()
    return sum(1 for token in query.lower().split() if token in haystack)


def _hybrid_search(params: WardrobeSearchInput) -> tuple[list[Any], dict[str, Any]]:
    settings = _settings()
    with database_session(settings.database_dsn) as connection:
        items = list_items(connection, params.user_id)
        if params.item_types:
            allowed_types = {value.lower() for value in params.item_types}
            items = [item for item in items if item.item_type.lower() in allowed_types]
        if params.colors:
            allowed_colors = {value.lower() for value in params.colors}
            items = [item for item in items if item.color.lower() in allowed_colors]
        try:
            from styleforge.api import get_multi_task_workflow

            retriever = get_multi_task_workflow().wardrobe_retriever
            if retriever is None:
                raise RuntimeError("hybrid retriever is unavailable")
            outcome = retriever.search(
                params.query,
                items,
                connection=connection,
                limit=params.limit,
            )
            by_id = {item.item_id: item for item in items}
            selected = [by_id[item_id] for item_id in outcome.item_ids if item_id in by_id]
            diagnostics = {
                "mode": outcome.mode,
                "semantic_available": outcome.semantic_available,
                "matched": outcome.matched,
            }
        except Exception as error:
            scored = [
                (score, item)
                for item in items
                if (score := _keyword_score(item, params.query)) > 0
            ]
            scored.sort(key=lambda pair: (-pair[0], pair[1].item_id))
            selected = [item for _, item in scored[: params.limit]]
            diagnostics = {
                "mode": "keyword",
                "semantic_available": False,
                "matched": len(scored),
                "fallback_reason": type(error).__name__,
            }
    return selected, diagnostics


def build_styleforge_mcp() -> FastMCP:
    mcp = FastMCP(
        "styleforge_mcp",
        instructions=(
            "Use StyleForge tools for grounded personal-wardrobe styling. "
            "Always scope wardrobe and task reads by user_id."
        ),
        host="127.0.0.1",
        stateless_http=True,
        json_response=True,
    )

    @mcp.tool(
        name="styleforge_get_system_status",
        title="Get StyleForge System Status",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def get_system_status() -> dict[str, Any]:
        """Return safe runtime, database, retrieval, weather and MCP status.

        This is read-only and excludes database credentials, API keys, user
        prompts and external-tool arguments.
        """
        from styleforge.api import health

        return health()

    @mcp.tool(
        name="styleforge_get_wardrobe_summary",
        title="Get StyleForge Wardrobe Summary",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def get_wardrobe_summary(
        user_id: Annotated[
            str,
            Field(
                min_length=1,
                max_length=128,
                description="StyleForge user identifier, for example demo-user.",
            ),
        ],
    ) -> dict[str, Any]:
        """Summarize one user's active wardrobe and preference weights.

        Returns total item count plus slot, item-type and color distributions.
        It never returns another user's items and does not modify data.
        """
        params = WardrobeUserInput(user_id=user_id)
        settings = _settings()
        with database_session(settings.database_dsn) as connection:
            items = list_items(connection, params.user_id)
            weights = get_evaluation_weights(connection, params.user_id)
        slot_counts = Counter(infer_slot(item.item_type) for item in items)
        type_counts = Counter(item.item_type for item in items)
        color_counts = Counter(item.color for item in items if item.color)
        return {
            "user_id": params.user_id,
            "item_count": len(items),
            "slot_counts": dict(sorted(slot_counts.items())),
            "item_type_counts": dict(type_counts.most_common(30)),
            "top_colors": dict(color_counts.most_common(20)),
            "evaluation_weights": weights,
        }

    @mcp.tool(
        name="styleforge_search_wardrobe",
        title="Search StyleForge Wardrobe",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def search_wardrobe(
        user_id: Annotated[str, Field(min_length=1, max_length=128)],
        query: Annotated[
            str,
            Field(
                min_length=1,
                max_length=240,
                description="Natural-language garment search, such as 白色通勤衬衫.",
            ),
        ],
        item_types: Annotated[
            list[str] | None,
            Field(description="Optional exact catalog item_type filters."),
        ] = None,
        colors: Annotated[
            list[str] | None,
            Field(description="Optional case-insensitive color filters."),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=20)] = 10,
    ) -> dict[str, Any]:
        """Search one user's active wardrobe using the deployed hybrid retriever.

        Semantic FashionCLIP/text retrieval is preferred. If model artifacts
        are unavailable the tool explicitly reports keyword fallback. Results
        are capped at 20 and contain grounded catalog identifiers only.
        """
        params = WardrobeSearchInput(
            user_id=user_id,
            query=query,
            item_types=item_types or [],
            colors=colors or [],
            limit=limit,
        )
        items, diagnostics = _hybrid_search(params)
        return {
            "user_id": params.user_id,
            "query": params.query,
            "count": len(items),
            "limit": params.limit,
            "items": [_safe_item(item) for item in items],
            "retrieval": diagnostics,
        }

    @mcp.tool(
        name="styleforge_execute_task",
        title="Execute StyleForge Styling Task",
        annotations=_EXECUTE,
        structured_output=True,
    )
    def execute_task(
        user_id: Annotated[str, Field(min_length=1, max_length=128)],
        request: Annotated[
            str,
            Field(
                min_length=1,
                max_length=2000,
                description="The user's styling request in natural language.",
            ),
        ],
        task_type: Annotated[
            TaskType | None,
            Field(description="Optional explicit StyleForge task type."),
        ] = None,
        max_results: Annotated[int, Field(ge=1, le=5)] = 3,
        current_outfit_id: Annotated[str, Field(max_length=128)] = "",
        current_item_ids: list[str] | None = None,
        target_slot: Annotated[str, Field(max_length=32)] = "",
        item_id: Annotated[str, Field(max_length=128)] = "",
        selected_item_id: Annotated[str, Field(max_length=128)] = "",
        candidate_item: CandidateItem | None = None,
        session_id: Annotated[str, Field(max_length=128)] = "",
    ) -> dict[str, Any]:
        """Execute one of StyleForge's six routed multi-Agent styling tasks.

        The call may use DeepSeek, Time/Fetch MCP, wardrobe RAG and preference
        memory. It persists a task run and may extract preference evidence, so
        it is not idempotent. Use ``task_type`` only when the caller knows the
        exact route; otherwise the deterministic router chooses it.
        """
        params = ExecuteTaskInput(
            user_id=user_id,
            request=request,
            task_type=task_type,
            max_results=max_results,
            current_outfit_id=current_outfit_id,
            current_item_ids=current_item_ids or [],
            target_slot=target_slot,
            item_id=item_id,
            selected_item_id=selected_item_id,
            candidate_item=candidate_item,
            session_id=session_id,
        )
        from styleforge.api import get_multi_task_workflow

        try:
            return get_multi_task_workflow().execute(params.to_domain())
        except Exception as error:
            raise ValueError(
                f"StyleForge task failed ({type(error).__name__}); inspect API health and retry."
            ) from error

    @mcp.tool(
        name="styleforge_get_task_result",
        title="Get StyleForge Task Result",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def get_task_result(
        user_id: Annotated[str, Field(min_length=1, max_length=128)],
        run_id: Annotated[str, Field(min_length=1, max_length=128)],
    ) -> dict[str, Any]:
        """Read a persisted task run owned by ``user_id``.

        Returns the stored context/result/diagnostics, including safe MCP call
        traces when external tools participated. A mismatched user is reported
        exactly like a missing run to preserve tenant isolation.
        """
        params = TaskResultInput(user_id=user_id, run_id=run_id)
        settings = _settings()
        with database_session(settings.database_dsn) as connection:
            payload = get_task_run(
                connection,
                user_id=params.user_id,
                run_id=params.run_id,
            )
        if payload is None:
            raise ValueError("Task run not found for this user_id.")
        return payload

    @mcp.resource(
        "styleforge://capabilities",
        name="styleforge_capabilities",
        title="StyleForge MCP Capabilities",
        description="Stable tool, task-type, transport and safety metadata.",
        mime_type="application/json",
    )
    def capabilities() -> str:
        return json.dumps(
            {
                "server": "styleforge_mcp",
                "task_types": [task.value for task in TaskType],
                "tools": list(STYLEFORGE_MCP_TOOL_NAMES),
                "transports": ["stdio", "streamable_http"],
                "external_mcp": ["official-time", "official-fetch"],
                "safety": {
                    "tenant_scoped": True,
                    "fetch_host_allowlist": True,
                    "raw_database_access": False,
                    "raw_filesystem_access": False,
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    @mcp.prompt(
        name="styleforge_plan_outfit",
        title="Plan a Grounded Wardrobe Outfit",
        description="Guide an MCP client through a grounded StyleForge recommendation.",
    )
    def plan_outfit(user_id: str, request: str) -> str:
        return (
            f"为用户 {user_id} 处理请求：{request}\n"
            "先调用 styleforge_get_wardrobe_summary 确认衣柜，再按需调用 "
            "styleforge_search_wardrobe，最后调用 styleforge_execute_task。"
            "不得编造衣物 ID；天气和 MCP 降级情况以 diagnostics 为准。"
        )

    return mcp


styleforge_mcp = build_styleforge_mcp()


def mounted_mcp_app():
    """Return an ASGI app mounted at ``/mcp`` by the main FastAPI process."""
    styleforge_mcp.settings.streamable_http_path = "/"
    return styleforge_mcp.streamable_http_app()


def server_status() -> dict[str, Any]:
    return {
        "enabled": True,
        "name": "styleforge_mcp",
        "endpoint": "/mcp/",
        "transports": ["stdio", "streamable_http"],
        "tools": list(STYLEFORGE_MCP_TOOL_NAMES),
        "resources": list(STYLEFORGE_MCP_RESOURCE_URIS),
        "prompts": list(STYLEFORGE_MCP_PROMPT_NAMES),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18001)
    parser.add_argument("--http-path", default="/mcp")
    args = parser.parse_args()
    styleforge_mcp.settings.host = args.host
    styleforge_mcp.settings.port = args.port
    if args.transport == "streamable-http":
        styleforge_mcp.settings.streamable_http_path = args.http_path
    styleforge_mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
