"""Build a traceable Context Pack from request, wardrobe, and memory."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from styleforge.core.categories import infer_slot
from styleforge.models.context import (
    ContextPack,
    OutfitContext,
    RequestContext,
    UserContext,
)
from styleforge.models.task import TaskExecutionInput
from styleforge.orchestration.task_router import TaskRoute
from styleforge.repositories.database import database_session
from styleforge.repositories.request_memory_repository import recent_request_memories
from styleforge.repositories.user_preferences_repository import get_evaluation_weights
from styleforge.repositories.wardrobe_repository import list_items


class ContextPackBuilder:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def build(
        self,
        task_input: TaskExecutionInput,
        route: TaskRoute,
        *,
        current_outfit_id: str | None = None,
        current_item_ids: list[str] | None = None,
        locked_item_ids: list[str] | None = None,
        editable_slots: list[str] | None = None,
    ) -> ContextPack:
        with database_session(self.database_path) as connection:
            wardrobe = list_items(connection, task_input.user_id)
            weights = get_evaluation_weights(connection, task_input.user_id)
            recent = recent_request_memories(connection, task_input.user_id, limit=5)
        slot_counts = Counter(infer_slot(item.item_type) for item in wardrobe)
        type_counts = Counter(item.item_type for item in wardrobe)
        wardrobe_summary = {
            "item_count": len(wardrobe),
            "slot_counts": dict(sorted(slot_counts.items())),
            "item_type_counts": dict(sorted(type_counts.items())),
        }
        return ContextPack(
            request_context=RequestContext(
                original_request=task_input.request,
                task_type=route.task_type.value,
                route_reason=route.reason,
                route_confidence=route.confidence,
            ),
            outfit_context=OutfitContext(
                current_outfit_id=current_outfit_id or task_input.current_outfit_id,
                current_item_ids=current_item_ids or list(task_input.current_item_ids),
                locked_item_ids=locked_item_ids or [],
                editable_slots=editable_slots or [],
            ),
            user_context=UserContext(
                user_id=task_input.user_id,
                wardrobe_summary=wardrobe_summary,
                preferences={"evaluation_profile": {"weights": weights}},
                recent_requests=recent,
            ),
            candidate_item=(
                task_input.candidate_item.model_dump(mode="json")
                if task_input.candidate_item is not None
                else None
            ),
        )
