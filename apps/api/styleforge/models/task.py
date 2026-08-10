"""Domain input shared by the six task subgraphs."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from styleforge.orchestration.task_router import TaskType
from styleforge.tools.weather.schemas import DeviceLocationContext


class CandidateItem(BaseModel):
    """A transient item being considered; it is never inserted into the wardrobe."""

    item_id: str = Field(default="candidate-preview", max_length=128)
    name: str = Field(default="", max_length=200)
    item_type: str = Field(min_length=1, max_length=64)
    subtype: str = Field(default="", max_length=64)
    color: str = Field(default="", max_length=64)
    description: str = Field(default="", max_length=1000)
    gender: str = Field(default="", max_length=16)


class TaskExecutionInput(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    request: str = Field(min_length=1, max_length=2000)
    max_results: int = Field(default=3, ge=1, le=10)
    requested_task_type: TaskType | None = None
    current_outfit_id: str = Field(default="", max_length=128)
    current_item_ids: list[str] = Field(default_factory=list, max_length=12)
    target_slot: str = Field(default="", max_length=32)
    item_id: str = Field(default="", max_length=128)
    candidate_item: CandidateItem | None = None
    location_context: DeviceLocationContext | None = None

    @model_validator(mode="after")
    def _deduplicate_item_ids(self) -> "TaskExecutionInput":
        self.current_item_ids = list(dict.fromkeys(self.current_item_ids))
        return self
