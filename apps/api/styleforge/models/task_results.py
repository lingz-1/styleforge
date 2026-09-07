"""Task-specific result contracts emitted after the shared three-agent chain."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from styleforge.orchestration.task_router import TaskType


TaskStatus = Literal["completed", "infeasible", "needs_clarification"]


class ExtensionResultModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class OutfitReference(ExtensionResultModel):
    outfit_id: str
    item_ids: list[str] = Field(min_length=2)
    reasoning: str = Field(min_length=1)


class CompatibilityOutfitReference(ExtensionResultModel):
    outfit_id: str
    wardrobe_item_ids: list[str] = Field(min_length=1)
    reasoning: str = Field(min_length=1)


class OutfitModifyResult(ExtensionResultModel):
    status: TaskStatus
    current_outfit_id: str = ""
    target_slot: str
    replaced_item_ids: list[str] = Field(default_factory=list)
    added_item_ids: list[str] = Field(default_factory=list)
    locked_item_ids: list[str] = Field(default_factory=list)
    alternatives: list[OutfitReference] = Field(default_factory=list)
    message: str

    @model_validator(mode="after")
    def _validate_completed_modification(self) -> "OutfitModifyResult":
        if self.status == "completed" and not self.alternatives:
            raise ValueError("completed outfit modification requires at least one alternative")
        return self


class StyleAdviceResult(ExtensionResultModel):
    status: TaskStatus
    knowledge_type: Literal["style"] = "style"
    title: str
    summary: str = Field(min_length=1)
    principles: list[dict[str, Any]] = Field(default_factory=list)
    wardrobe_matches: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_completed_advice(self) -> "StyleAdviceResult":
        if self.status == "completed" and not self.principles:
            raise ValueError("completed style advice requires at least one principle")
        return self


class ItemAdviceResult(ExtensionResultModel):
    status: TaskStatus
    knowledge_type: Literal["item"] = "item"
    title: str
    summary: str = Field(min_length=1)
    anchor_item: dict[str, Any] | None = None
    anchor_source: Literal["wardrobe", "candidate", "unresolved"] = "unresolved"
    compatible_items_by_slot: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    wardrobe_matches: list[dict[str, Any]] = Field(default_factory=list)
    wardrobe_matches_by_slot: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    sample_outfits: list[OutfitReference] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    clarification_question: str = ""
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_business_completion(self) -> "ItemAdviceResult":
        if self.status == "needs_clarification":
            if not self.clarification_question.strip():
                raise ValueError("needs_clarification requires clarification_question")
            return self
        if self.status != "completed":
            return self
        if self.anchor_item is None or self.anchor_source == "unresolved":
            raise ValueError("completed item advice requires a resolved anchor_item")
        if not any(self.compatible_items_by_slot.values()):
            raise ValueError("completed item advice requires compatible wardrobe items")
        if not self.sample_outfits:
            raise ValueError("completed item advice requires at least one sample_outfit")
        anchor_id = str(self.anchor_item.get("item_id", ""))
        if not anchor_id:
            raise ValueError("completed item advice anchor requires item_id")
        for outfit in self.sample_outfits:
            if anchor_id not in outfit.item_ids:
                raise ValueError("every sample_outfit must include the anchor item")
        return self


class WardrobeCompatibilityResult(ExtensionResultModel):
    status: TaskStatus
    candidate_item: dict[str, Any]
    candidate_slot: str
    compatibility_score: float = Field(ge=0, le=100)
    recommendation: Literal["recommended", "consider", "not_recommended", "unknown"]
    recommendation_text: str
    compatible_item_counts: dict[str, int] = Field(default_factory=dict)
    compatible_items_by_slot: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    complete_outfit_count: int = Field(ge=0)
    sample_outfits: list[CompatibilityOutfitReference] = Field(default_factory=list)
    redundancy_score: float = Field(ge=0, le=100)
    similar_wardrobe_items: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    clarification_question: str = ""
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_completed_compatibility(self) -> "WardrobeCompatibilityResult":
        if self.status == "needs_clarification" and not self.clarification_question.strip():
            raise ValueError("needs_clarification requires clarification_question")
        if self.status == "completed" and self.complete_outfit_count > 0:
            if not self.sample_outfits:
                raise ValueError("positive complete_outfit_count requires sample_outfits")
        return self


class WardrobeGapResult(ExtensionResultModel):
    status: TaskStatus
    analysis_mode: Literal["targeted", "general"]
    target: dict[str, Any] = Field(default_factory=dict)
    wardrobe_item_count: int = Field(ge=0)
    slot_counts: dict[str, int] = Field(default_factory=dict)
    covered_elements: list[dict[str, Any]] = Field(default_factory=list)
    gaps: list[dict[str, Any]] = Field(default_factory=list)
    gap_count: int = Field(ge=0)
    redundancies: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = Field(min_length=1)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_gap_count(self) -> "WardrobeGapResult":
        if self.gap_count != len(self.gaps):
            raise ValueError("gap_count must equal the number of gaps")
        return self


RESULT_MODELS: dict[TaskType, type[BaseModel]] = {
    TaskType.OUTFIT_MODIFY: OutfitModifyResult,
    TaskType.STYLE_ADVICE: StyleAdviceResult,
    TaskType.ITEM_ADVICE: ItemAdviceResult,
    TaskType.WARDROBE_COMPATIBILITY: WardrobeCompatibilityResult,
    TaskType.WARDROBE_GAP: WardrobeGapResult,
}


def validate_task_result(task_type: TaskType, payload: dict[str, Any]) -> dict[str, Any]:
    model = RESULT_MODELS.get(task_type)
    if model is None:
        return payload
    return model.model_validate(payload).model_dump(mode="json")
