"""Pydantic contracts for the three semantic agents' structured outputs.

The field shapes follow the frozen v3.2.1-final spec. The ``query`` values in
``RetrievalPlan`` are English short phrases that feed FashionCLIP.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from styleforge.llm.client import LlmInvalidJson, LlmSchemaViolation
from styleforge.tools.weather.schemas import ContextRequirements

PLAN_TYPES = ("core", "distinctive", "supporting")
DECISIONS = ("accept", "recompose", "retrieve_more", "wardrobe_gap")


class RequestSignature(BaseModel):
    theme: str
    explicit_style: list[str] = Field(default_factory=list, max_length=6)
    unique_mood: list[str] = Field(default_factory=list, max_length=6)
    practical_context: list[str] = Field(default_factory=list, max_length=6)
    generic_tendencies_to_avoid: list[str] = Field(default_factory=list, min_length=1, max_length=5)


class RetrievalPlan(BaseModel):
    type: Literal["core", "distinctive", "supporting"]
    query: str = Field(min_length=2, max_length=160)
    score_weight: float = Field(ge=0.0, le=1.0)


class CandidateRequirements(BaseModel):
    tops: int = Field(default=8, ge=0, le=50)
    bottoms: int = Field(default=8, ge=0, le=50)
    dresses: int = Field(default=0, ge=0, le=50)
    outerwear: int = Field(default=0, ge=0, le=50)
    shoes: int = Field(default=8, ge=0, le=50)
    accessories: int = Field(default=0, ge=0, le=50)

    def to_dict(self) -> dict[str, int]:
        return {
            "tops": self.tops,
            "bottoms": self.bottoms,
            "dresses": self.dresses,
            "outerwear": self.outerwear,
            "shoes": self.shoes,
            "accessories": self.accessories,
        }

    @model_validator(mode="after")
    def _bound_total(self) -> "CandidateRequirements":
        if sum(self.to_dict().values()) > 60:
            raise ValueError(f"candidate_requirements total quota exceeds 60: {sum(self.to_dict().values())}")
        return self


class Agent1Output(BaseModel):
    request_signature: RequestSignature
    retrieval_plans: list[RetrievalPlan]
    candidate_requirements: CandidateRequirements
    context_requirements: ContextRequirements = Field(
        default_factory=ContextRequirements
    )
    implicit_context_signals: list[str] = Field(default_factory=list, max_length=6)
    context_criticality: Literal["required", "helpful", "not_needed"] = "not_needed"
    uncertainties: list[str] = Field(default_factory=list, max_length=6)
    default_policy_allowed: bool = False

    @model_validator(mode="after")
    def _exactly_three_plans(self) -> "Agent1Output":
        if len(self.retrieval_plans) != len(PLAN_TYPES):
            raise ValueError(
                f"retrieval_plans must contain exactly {len(PLAN_TYPES)} plans, got {len(self.retrieval_plans)}"
            )
        seen = {plan.type for plan in self.retrieval_plans}
        if seen != set(PLAN_TYPES):
            raise ValueError(f"retrieval_plans must cover types {PLAN_TYPES}, got {sorted(seen)}")
        return self

    def to_dict(self) -> dict[str, object]:
        return {
            "request_signature": self.request_signature.model_dump(),
            "retrieval_plans": [plan.model_dump() for plan in self.retrieval_plans],
            "candidate_requirements": self.candidate_requirements.to_dict(),
            "context_requirements": self.context_requirements.model_dump(mode="json"),
            "implicit_context_signals": list(self.implicit_context_signals),
            "context_criticality": self.context_criticality,
            "uncertainties": list(self.uncertainties),
            "default_policy_allowed": self.default_policy_allowed,
        }


class CompositionStrategy(BaseModel):
    visual_anchor: str = Field(min_length=1)
    supporting_direction: str = Field(min_length=1)
    practical_balance: str = Field(min_length=1)

    def to_dict(self) -> dict[str, str]:
        return {
            "visual_anchor": self.visual_anchor,
            "supporting_direction": self.supporting_direction,
            "practical_balance": self.practical_balance,
        }


class RequestSpecificElement(BaseModel):
    item_id: str
    role: str


class EnvironmentAdjustment(BaseModel):
    """A factual, grounded outfit change caused by weather/environment.

    ``fact_refs`` must reference a real fact key from the environment context,
    and ``wardrobe_item_ids`` must reference items inside this proposal.
    """

    fact_refs: list[str] = Field(default_factory=list)
    impact: str = Field(default="", min_length=1)
    action: str = Field(default="", min_length=1)
    wardrobe_item_ids: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "fact_refs": list(self.fact_refs),
            "impact": self.impact,
            "action": self.action,
            "wardrobe_item_ids": list(self.wardrobe_item_ids),
        }


class CarryRecommendation(BaseModel):
    """An external item to bring (umbrella, water, ...). Never a wardrobe item."""

    name: str = Field(min_length=1, max_length=32)
    reason: str = Field(default="", min_length=1, max_length=240)
    fact_refs: list[str] = Field(default_factory=list)
    category: Literal["external_carry_item"] = "external_carry_item"

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "reason": self.reason,
            "fact_refs": list(self.fact_refs),
            "category": self.category,
        }


class OutfitProposal(BaseModel):
    outfit_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    composition_strategy: CompositionStrategy
    item_ids: list[str] = Field(min_length=2, max_length=8)
    style_tag: str = ""
    reasoning: str = ""
    request_specific_elements: list[RequestSpecificElement] = Field(default_factory=list)
    environment_adjustments: list[EnvironmentAdjustment] = Field(default_factory=list)
    carry_recommendations: list[CarryRecommendation] = Field(default_factory=list)

    @field_validator("item_ids")
    @classmethod
    def _no_duplicate_items(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("outfit item_ids must not contain duplicates")
        return value

    def to_dict(self) -> dict[str, object]:
        return {
            "outfit_id": self.outfit_id,
            "composition_strategy": self.composition_strategy.to_dict(),
            "item_ids": list(self.item_ids),
            "style_tag": self.style_tag,
            "reasoning": self.reasoning,
            "request_specific_elements": [element.model_dump() for element in self.request_specific_elements],
            "environment_adjustments": [
                adjustment.to_dict() for adjustment in self.environment_adjustments
            ],
            "carry_recommendations": [
                recommendation.to_dict() for recommendation in self.carry_recommendations
            ],
        }


class Agent2Output(BaseModel):
    outfits: list[OutfitProposal] = Field(min_length=3, max_length=5)

    @model_validator(mode="after")
    def _three_to_five_outfits(self) -> "Agent2Output":
        if not 3 <= len(self.outfits) <= 5:
            raise ValueError(f"composer must return 3-5 outfits, got {len(self.outfits)}")
        return self

    def to_dict(self) -> dict[str, object]:
        return {"outfits": [outfit.to_dict() for outfit in self.outfits]}


class DimensionScores(BaseModel):
    request_relevance: int = Field(ge=1, le=10)
    request_specificity: int = Field(ge=1, le=10)
    outfit_coordination: int = Field(ge=1, le=10)
    wearability: int = Field(ge=1, le=10)
    freshness: int = Field(ge=1, le=10)

    def to_dict(self) -> dict[str, int]:
        return {
            "request_relevance": self.request_relevance,
            "request_specificity": self.request_specificity,
            "outfit_coordination": self.outfit_coordination,
            "wearability": self.wearability,
            "freshness": self.freshness,
        }


class OutfitAssessment(BaseModel):
    outfit_id: str
    dimension_scores: DimensionScores
    reasoning: str = ""
    improvements: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "outfit_id": self.outfit_id,
            "dimension_scores": self.dimension_scores.to_dict(),
            "reasoning": self.reasoning,
            "improvements": self.improvements,
        }


class ExplanationAssessment(BaseModel):
    grounded: bool = True
    unsupported_claims: list[str] = Field(default_factory=list)


class Alternative(BaseModel):
    outfit_id: str
    strength: str = ""
    dimension_scores: DimensionScores | None = None


class WardrobeGapItem(BaseModel):
    category: str
    desired_features: list[str] = Field(default_factory=list)


class EnvironmentAssessment(BaseModel):
    """Agent 3's review of weather/environment claims and carry advice."""

    grounded: bool = True
    coverage_complete: bool = True
    unsupported_claims: list[str] = Field(default_factory=list)
    missing_adjustments: list[str] = Field(default_factory=list)
    carry_advice_grounded: bool = True


class CriticOutput(BaseModel):
    outfit_assessment: OutfitAssessment
    explanation_assessment: ExplanationAssessment = Field(default_factory=ExplanationAssessment)
    environment_assessment: EnvironmentAssessment = Field(
        default_factory=EnvironmentAssessment
    )
    alternatives: list[Alternative] = Field(default_factory=list)
    decision: Literal["accept", "recompose", "retrieve_more", "wardrobe_gap"]
    failure_source: Literal["", "composer", "candidate_pool", "wardrobe"] = ""
    feedback: str = ""
    missing_items: list[WardrobeGapItem] = Field(default_factory=list)
    best_effort_outfit_id: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "outfit_assessment": self.outfit_assessment.to_dict(),
            "explanation_assessment": self.explanation_assessment.model_dump(),
            "environment_assessment": self.environment_assessment.model_dump(),
            "alternatives": [alternative.model_dump() for alternative in self.alternatives],
            "decision": self.decision,
            "failure_source": self.failure_source,
            "feedback": self.feedback,
            "missing_items": [item.model_dump() for item in self.missing_items],
            "best_effort_outfit_id": self.best_effort_outfit_id,
        }


def parse_llm_json(raw: str, model: type[BaseModel]) -> BaseModel:
    """Parse raw JSON text into a validated model.

    Raises ``LlmInvalidJson`` on parse failure and ``LlmSchemaViolation`` on
    schema validation failure.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as error:
        raise LlmInvalidJson(f"{type(error).__name__}: {error}") from error
    try:
        return model.model_validate(data)
    except Exception as error:
        raise LlmSchemaViolation(f"{type(error).__name__}: {error}") from error
