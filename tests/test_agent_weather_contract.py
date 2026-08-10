"""Offline tests for the V2.1 agent weather contract and sanitization."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from styleforge.agents.composer import sanitize_environment_fields
from styleforge.llm.prompts import PROMPT_VERSION
from styleforge.llm.schema import (
    Agent1Output,
    CarryRecommendation,
    CriticOutput,
    EnvironmentAdjustment,
    EnvironmentAssessment,
    OutfitProposal,
)


def _agent1_payload() -> dict:
    return {
        "request_signature": {
            "theme": "黑色马甲怎么搭",
            "explicit_style": [],
            "unique_mood": ["干练"],
            "practical_context": [],
            "generic_tendencies_to_avoid": ["缺少视觉重点"],
        },
        "retrieval_plans": [
            {"type": "core", "query": "black vest layering", "score_weight": 0.4},
            {"type": "distinctive", "query": "chic black waistcoat", "score_weight": 0.3},
            {"type": "supporting", "query": "neutral tops and bottoms", "score_weight": 0.1},
        ],
        "candidate_requirements": {
            "tops": 8,
            "bottoms": 8,
            "dresses": 0,
            "outerwear": 0,
            "shoes": 8,
            "accessories": 0,
        },
        "context_requirements": {
            "weather": {"needed": False},
            "temporal": {"needed": False},
            "location": {"needed": False, "allow_profile_default": True},
        },
    }


def _proposal(item_ids: list[str]) -> dict:
    return {
        "outfit_id": "outfit_001",
        "composition_strategy": {
            "visual_anchor": "结构感外套",
            "supporting_direction": "浅色内搭",
            "practical_balance": "实穿",
        },
        "item_ids": item_ids,
        "style_tag": "剧场复古",
        "reasoning": "结构化轮廓",
        "environment_adjustments": [],
        "carry_recommendations": [],
    }


def test_agent1_new_context_fields_default_to_safe_values() -> None:
    output = Agent1Output.model_validate(_agent1_payload())

    assert output.context_criticality == "not_needed"
    assert output.default_policy_allowed is False
    assert output.implicit_context_signals == []
    assert output.uncertainties == []
    payload = output.to_dict()
    assert payload["context_criticality"] == "not_needed"
    assert payload["default_policy_allowed"] is False


def test_agent1_parses_implicit_weather_intent() -> None:
    payload = _agent1_payload()
    payload["context_requirements"]["weather"] = {
        "needed": True,
        "location": "北京",
        "date": "明天",
        "granularity": "daily",
        "reason": "旅游出行受天气影响",
    }
    payload["context_requirements"]["temporal"] = {
        "needed": True,
        "expression": "明天",
        "reason": "需要确定日期的天气",
    }
    payload["context_requirements"]["location"] = {
        "needed": True,
        "query": "北京",
        "allow_profile_default": True,
        "reason": "天气查询需要地点",
    }
    payload["implicit_context_signals"] = ["未显式提天气但隐含出行日期地点"]
    payload["context_criticality"] = "helpful"
    payload["default_policy_allowed"] = True

    output = Agent1Output.model_validate(payload)

    assert output.context_requirements.weather.needed is True
    assert output.context_requirements.weather.granularity == "daily"
    assert output.context_requirements.temporal.expression == "明天"
    assert output.context_requirements.location.query == "北京"
    assert output.context_criticality == "helpful"
    assert output.default_policy_allowed is True


def test_outfit_proposal_parses_environment_adjustments_and_carry() -> None:
    payload = _proposal(["coat_07", "top_03"])
    payload["environment_adjustments"] = [
        {
            "fact_refs": ["weather:day:2026-08-11:precipitation"],
            "impact": "返程时段降水概率高",
            "action": "加入防水外层",
            "wardrobe_item_ids": ["coat_07"],
        }
    ]
    payload["carry_recommendations"] = [
        {"name": "折叠伞", "reason": "可能有阵雨", "fact_refs": ["weather:day:2026-08-11:precipitation"]}
    ]

    output = OutfitProposal.model_validate(payload)

    adjustment = output.environment_adjustments[0]
    assert adjustment.fact_refs == ["weather:day:2026-08-11:precipitation"]
    assert adjustment.wardrobe_item_ids == ["coat_07"]
    assert output.carry_recommendations[0].category == "external_carry_item"
    dumped = output.to_dict()
    assert dumped["environment_adjustments"][0]["impact"] == "返程时段降水概率高"
    assert dumped["carry_recommendations"][0]["category"] == "external_carry_item"


def test_carry_recommendation_category_is_locked() -> None:
    carry = CarryRecommendation(name="遮阳帽", reason="晴天", fact_refs=["weather:day:2026-08-11:uv"])
    assert carry.category == "external_carry_item"

    with pytest.raises(ValidationError):
        CarryRecommendation(name="帽子", reason="遮挡", category="wardrobe")


def test_critic_environment_assessment_defaults_grounded() -> None:
    output = CriticOutput(
        outfit_assessment={
            "outfit_id": "outfit_001",
            "dimension_scores": {
                "request_relevance": 8,
                "request_specificity": 7,
                "outfit_coordination": 8,
                "wearability": 8,
                "freshness": 7,
            },
        },
        decision="accept",
    )

    assert output.environment_assessment.grounded is True
    assert output.environment_assessment.coverage_complete is True
    assert output.environment_assessment.carry_advice_grounded is True
    assert isinstance(output.environment_assessment, EnvironmentAssessment)
    assert "environment_assessment" in output.to_dict()


def test_sanitize_drops_out_of_pool_and_unreferenced_adjustments() -> None:
    proposal = OutfitProposal.model_validate(
        _proposal(["coat_07", "top_03"])
    ).model_copy(
        update={
            "environment_adjustments": [
                EnvironmentAdjustment(
                    fact_refs=["weather:day:x"],
                    impact="a",
                    action="b",
                    wardrobe_item_ids=["coat_07"],
                ),
                EnvironmentAdjustment(
                    fact_refs=["weather:day:x"],
                    impact="a",
                    action="b",
                    wardrobe_item_ids=["not_in_pool"],
                ),
                EnvironmentAdjustment(
                    fact_refs=[],
                    impact="a",
                    action="b",
                    wardrobe_item_ids=["coat_07"],
                ),
            ]
        }
    )

    cleaned, dropped = sanitize_environment_fields(proposal)

    assert dropped == 2
    assert len(cleaned.environment_adjustments) == 1
    assert cleaned.environment_adjustments[0].wardrobe_item_ids == ["coat_07"]


def test_sanitize_keeps_proposal_untouched_when_all_grounded() -> None:
    proposal = OutfitProposal.model_validate(
        _proposal(["coat_07", "top_03"])
    ).model_copy(
        update={
            "environment_adjustments": [
                EnvironmentAdjustment(
                    fact_refs=["weather:day:x"],
                    impact="a",
                    action="b",
                    wardrobe_item_ids=["top_03"],
                )
            ]
        }
    )

    cleaned, dropped = sanitize_environment_fields(proposal)

    assert dropped == 0
    assert len(cleaned.environment_adjustments) == 1


def test_prompt_version_bumped_for_weather_v2_1() -> None:
    assert PROMPT_VERSION == "2026.08.10-weather-v2.1"
