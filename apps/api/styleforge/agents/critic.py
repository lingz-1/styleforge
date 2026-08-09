"""Critic agent (Agent 3): single-call two-phase review + decision routing.

The LLM first blind-scores each outfit from item data only, then verifies the
composer's reasoning against item evidence, and finally emits one of the four
frozen decisions. On LLM failure it degrades to a deterministic critic that
checks wardrobe grounding and category completeness.
"""

from __future__ import annotations

import json
from typing import Any

from styleforge.core.schemas import TaskSpec
from styleforge.llm.client import LlmCallDiagnostics, LlmInvalidJson, LlmSchemaViolation, LlmUnavailable
from styleforge.llm.prompts import PROMPT_VERSION, build_agent3_prompt
from styleforge.llm.schema import (
    Alternative,
    CriticOutput,
    DimensionScores,
    ExplanationAssessment,
    OutfitAssessment,
    WardrobeGapItem,
    parse_llm_json,
)


def _neutral_assessment(outfit_id: str, reasoning: str) -> OutfitAssessment:
    return OutfitAssessment(
        outfit_id=outfit_id,
        dimension_scores=DimensionScores(
            request_relevance=7,
            request_specificity=6,
            coordination=7,
            wearability=8,
            freshness=6,
        ),
        reasoning=reasoning,
        improvements="",
    )


def _wardrobe_gap_output(
    outfit_id: str,
    feedback: str,
    missing_items: list[WardrobeGapItem],
) -> CriticOutput:
    return CriticOutput(
        outfit_assessment=_neutral_assessment(outfit_id, "衣橱缺少满足请求的必要品类"),
        explanation_assessment=ExplanationAssessment(grounded=True, unsupported_claims=[]),
        alternatives=[],
        decision="wardrobe_gap",
        failure_source="wardrobe",
        feedback=feedback,
        missing_items=missing_items,
        best_effort_outfit_id=outfit_id,
    )


def deterministic_critic(
    validated: list[dict[str, Any]],
    task: TaskSpec,
    wardrobe_ids: set[str],
) -> CriticOutput:
    """Rule-based decision when the LLM critic is unavailable."""
    grounded = [
        proposal
        for proposal in validated
        if set(proposal.get("item_ids", [])) <= wardrobe_ids
    ]
    if not grounded:
        missing = [
            WardrobeGapItem(category=slot, desired_features=[])
            for slot in task.required_slots
        ]
        return _wardrobe_gap_output(
            outfit_id="",
            feedback="推荐单品不在当前用户衣柜中，无法构成可行方案",
            missing_items=missing,
        )
    best = grounded[0]
    return CriticOutput(
        outfit_assessment=_neutral_assessment(
            best.get("outfit_id", ""),
            "确定性评审：方案通过基础校验且全部单品来自用户衣柜",
        ),
        explanation_assessment=ExplanationAssessment(grounded=True, unsupported_claims=[]),
        alternatives=[
            Alternative(outfit_id=proposal.get("outfit_id", ""), strength="备选方案")
            for proposal in grounded[1:3]
        ],
        decision="accept",
        failure_source="",
        feedback="",
        missing_items=[],
        best_effort_outfit_id="",
    )


class CriticAgent:
    """Agent 3 backend: LLM critic with deterministic fallback."""

    backend = "llm"

    def run(
        self,
        *,
        user_query: str,
        request_signature: dict[str, Any],
        outfits: list[dict[str, Any]],
        llm: Any,
        task: TaskSpec,
        wardrobe_ids: set[str],
        weights: dict[str, float] | None = None,
    ) -> tuple[CriticOutput, dict[str, Any], LlmCallDiagnostics | None]:
        if llm is None:
            output = deterministic_critic(outfits, task, wardrobe_ids)
            info = {
                "degraded": True,
                "reason": "llm client unavailable",
                "prompt_version": PROMPT_VERSION,
            }
            return output, info, None
        try:
            system, user = build_agent3_prompt(
                user_query=user_query,
                request_signature=request_signature,
                outfits=outfits,
                weights=weights,
            )
            payload, diagnostics = llm.chat_json(
                system=system,
                user=user,
                json_schema={},
            )
            output = parse_llm_json(json.dumps(payload), CriticOutput)
            info = {
                "degraded": False,
                "prompt_version": PROMPT_VERSION,
                "diagnostics": diagnostics.to_dict(),
            }
            return output, info, diagnostics
        except (LlmUnavailable, LlmInvalidJson, LlmSchemaViolation) as error:
            output = deterministic_critic(outfits, task, wardrobe_ids)
            info = {
                "degraded": True,
                "reason": f"{type(error).__name__}: {error}",
                "prompt_version": PROMPT_VERSION,
            }
            return output, info, None
