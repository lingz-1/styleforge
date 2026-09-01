"""Strict domain records shared by pipelines and repositories."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class ImageStatus(str, Enum):
    UNBOUND = "unbound"
    AVAILABLE = "available"
    MISSING = "missing"


class EmbeddingStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CatalogItem:
    item_id: str
    source: str
    gender: str
    item_type: str
    main_category: str
    name: str
    color: str
    description: str
    features: tuple[str, ...]
    image_filename: str
    relative_image_path: str
    image_status: ImageStatus
    embedding_status: EmbeddingStatus = EmbeddingStatus.PENDING
    raw_json_hash: str = ""
    dataset_item_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["image_status"] = self.image_status.value
        result["embedding_status"] = self.embedding_status.value
        result["features"] = list(self.features)
        return result


@dataclass(frozen=True, slots=True)
class TaskSpec:
    user_id: str
    occasion: str = "daily"
    target_audiences: tuple[str, ...] = ()
    required_slots: tuple[str, ...] = ("top", "bottom")
    required_item_types_by_slot: dict[str, tuple[str, ...]] = field(default_factory=dict)
    required_subtypes_by_slot: dict[str, tuple[str, ...]] = field(default_factory=dict)
    excluded_subtypes_by_slot: dict[str, tuple[str, ...]] = field(default_factory=dict)
    excluded_colors: tuple[str, ...] = ()
    excluded_item_ids: tuple[str, ...] = ()
    excluded_name_keywords: tuple[str, ...] = ()
    preferred_colors: tuple[str, ...] = ()
    max_results: int = 3

    def __post_init__(self) -> None:
        if not self.user_id.strip():
            raise ValueError("user_id cannot be empty")
        if not 1 <= self.max_results <= 10:
            raise ValueError("max_results must be between 1 and 10")
        for slot, item_types in self.required_item_types_by_slot.items():
            if not slot.strip() or not item_types or any(not value.strip() for value in item_types):
                raise ValueError("required item type constraints must not be empty")
        for constraints in (
            self.required_subtypes_by_slot,
            self.excluded_subtypes_by_slot,
        ):
            for slot, subtypes in constraints.items():
                if not slot.strip() or not subtypes or any(not value.strip() for value in subtypes):
                    raise ValueError("garment subtype constraints must not be empty")
        if any(not audience.strip() for audience in self.target_audiences):
            raise ValueError("target audiences must not be empty")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for key in (
            "target_audiences",
            "required_slots",
            "excluded_colors",
            "excluded_item_ids",
            "excluded_name_keywords",
            "preferred_colors",
        ):
            result[key] = list(result[key])
        result["required_item_types_by_slot"] = {
            slot: list(item_types)
            for slot, item_types in self.required_item_types_by_slot.items()
        }
        result["required_subtypes_by_slot"] = {
            slot: list(subtypes)
            for slot, subtypes in self.required_subtypes_by_slot.items()
        }
        result["excluded_subtypes_by_slot"] = {
            slot: list(subtypes)
            for slot, subtypes in self.excluded_subtypes_by_slot.items()
        }
        return result


@dataclass(frozen=True, slots=True)
class OutfitCandidate:
    outfit_id: str
    item_ids: tuple[str, ...]
    slot_items: dict[str, str]
    hard_valid: bool
    score: float
    llm_score: float | None = None
    rule_score: float | None = None
    score_details: dict[str, Any] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ImageBinding:
    relative_path: str
    absolute_path: Path | None
    status: ImageStatus


@dataclass(frozen=True, slots=True)
class CatalogItemImage:
    item_id: str
    position: int
    image_role: str
    image_filename: str
    relative_image_path: str
    image_status: ImageStatus
    is_primary: bool = False


@dataclass(frozen=True, slots=True)
class RecommendationResult:
    run_id: str
    status: str
    recommendations: tuple[OutfitCandidate, ...]
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "recommendations": [
                {
                    "outfit_id": candidate.outfit_id,
                    "item_ids": list(candidate.item_ids),
                    "slot_items": candidate.slot_items,
                    "hard_valid": candidate.hard_valid,
                    "score": candidate.score,
                    "llm_score": candidate.llm_score,
                    "rule_score": candidate.rule_score,
                    "score_details": candidate.score_details,
                    "reasons": list(candidate.reasons),
                }
                for candidate in self.recommendations
            ],
            "diagnostics": self.diagnostics,
        }


@dataclass(frozen=True, slots=True)
class StructureSignature:
    """Cross-request outfit structure fingerprint for diversity memory."""

    category_structure: tuple[str, ...] = ()
    dominant_color_family: tuple[str, ...] = ()
    style_mix: tuple[str, ...] = ()
    layer_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "category_structure": list(self.category_structure),
            "dominant_color_family": list(self.dominant_color_family),
            "style_mix": list(self.style_mix),
            "layer_count": self.layer_count,
        }
