import json

import pytest

from styleforge.llm.client import LlmInvalidJson, LlmSchemaViolation
from styleforge.llm.schema import (
    Agent1Output,
    Agent2Output,
    CriticOutput,
    parse_llm_json,
)


def _agent1_payload() -> dict:
    return {
        "request_signature": {
            "theme": "看《悲惨世界》音乐剧",
            "unique_mood": ["悲壮"],
            "practical_context": ["剧场"],
            "generic_tendencies_to_avoid": ["仅由基础款组成"],
        },
        "retrieval_plans": [
            {"type": "core", "query": "dark vintage theater outfit", "score_weight": 0.40},
            {"type": "distinctive", "query": "structured burgundy piece", "score_weight": 0.30},
            {"type": "supporting", "query": "comfortable semi-formal", "score_weight": 0.10},
        ],
        "candidate_requirements": {"tops": 10, "bottoms": 10, "dresses": 6, "outerwear": 8, "shoes": 8, "accessories": 8},
    }


def _agent2_payload() -> dict:
    return {
        "outfits": [
            {
                "outfit_id": "outfit_001",
                "composition_strategy": {
                    "visual_anchor": "结构感外套",
                    "supporting_direction": "深色下装",
                    "practical_balance": "舒适",
                },
                "item_ids": ["coat_07", "top_03", "bottom_05", "shoes_02"],
                "style_tag": "剧场复古",
                "reasoning": "炭灰大衣配合深酒红内搭",
                "request_specific_elements": [{"item_id": "coat_07", "role": "仪式感"}],
            }
        ]
    }


def _agent3_payload() -> dict:
    return {
        "outfit_assessment": {
            "outfit_id": "outfit_002",
            "dimension_scores": {
                "request_relevance": 9,
                "request_specificity": 8,
                "outfit_coordination": 9,
                "wearability": 8,
                "freshness": 7,
            },
            "reasoning": "深色结构感表达悲壮氛围",
            "improvements": "增加胸针",
        },
        "explanation_assessment": {"grounded": True, "unsupported_claims": []},
        "alternatives": [{"outfit_id": "outfit_001", "strength": "更实穿"}],
        "decision": "accept",
        "failure_source": "",
        "feedback": "",
        "missing_items": [],
        "best_effort_outfit_id": "",
    }


def test_agent1_valid() -> None:
    model = parse_llm_json(json.dumps(_agent1_payload()), Agent1Output)
    assert model.request_signature.theme == "看《悲惨世界》音乐剧"
    assert {plan.type for plan in model.retrieval_plans} == {"core", "distinctive", "supporting"}
    assert sum(model.candidate_requirements.to_dict().values()) == 50


def test_agent1_rejects_missing_plan_type() -> None:
    payload = _agent1_payload()
    payload["retrieval_plans"] = payload["retrieval_plans"][:2]
    with pytest.raises(LlmSchemaViolation):
        parse_llm_json(json.dumps(payload), Agent1Output)


def test_agent1_rejects_duplicate_plan_type() -> None:
    payload = _agent1_payload()
    payload["retrieval_plans"][1]["type"] = "core"
    with pytest.raises(LlmSchemaViolation):
        parse_llm_json(json.dumps(payload), Agent1Output)


def test_agent1_rejects_negative_weight() -> None:
    payload = _agent1_payload()
    payload["retrieval_plans"][0]["score_weight"] = -0.1
    with pytest.raises(LlmSchemaViolation):
        parse_llm_json(json.dumps(payload), Agent1Output)


def test_agent1_rejects_quota_over_60() -> None:
    payload = _agent1_payload()
    payload["candidate_requirements"]["tops"] = 61
    with pytest.raises(LlmSchemaViolation):
        parse_llm_json(json.dumps(payload), Agent1Output)


def test_agent2_rejects_less_than_three_outfits() -> None:
    payload = _agent2_payload()
    with pytest.raises(LlmSchemaViolation):
        parse_llm_json(json.dumps(payload), Agent2Output)


def test_agent2_rejects_duplicate_item_ids() -> None:
    payload = _agent2_payload()
    payload["outfits"][0]["item_ids"] = ["coat_07", "coat_07", "bottom_05", "shoes_02"]
    with pytest.raises(LlmSchemaViolation):
        parse_llm_json(json.dumps(payload), Agent2Output)


def test_agent3_valid() -> None:
    model = parse_llm_json(json.dumps(_agent3_payload()), CriticOutput)
    assert model.decision == "accept"
    assert model.outfit_assessment.dimension_scores.request_relevance == 9


def test_agent3_rejects_unknown_decision() -> None:
    payload = _agent3_payload()
    payload["decision"] = "reorder"
    with pytest.raises(LlmSchemaViolation):
        parse_llm_json(json.dumps(payload), CriticOutput)


def test_agent3_rejects_score_out_of_range() -> None:
    payload = _agent3_payload()
    payload["outfit_assessment"]["dimension_scores"]["freshness"] = 11
    with pytest.raises(LlmSchemaViolation):
        parse_llm_json(json.dumps(payload), CriticOutput)


def test_parse_llm_json_invalid() -> None:
    with pytest.raises(LlmInvalidJson):
        parse_llm_json("not json at all", CriticOutput)
