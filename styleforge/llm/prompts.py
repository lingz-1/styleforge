"""Prompt builders for the three semantic agents.

The builders return ``(system, user)`` message pairs. All retrieval query
strings are English short phrases because they feed the English FashionCLIP
model. Few-shot examples mirror the frozen v3.2.1-final spec.
"""

from __future__ import annotations

import json
from typing import Any

from styleforge.core.rubric import normalize_weights, rubric_text

PROMPT_VERSION = "2026.08.05"


# --- shared JSON envelope ---------------------------------------------------

_AGENT1_SYSTEM = (
    "你是一个个人穿搭语义检索师。你只负责理解用户的完整意图、产出请求画像、"
    "多类检索计划和品类配额，绝不编造衣橱中不存在的单品。\n"
    "输出必须是合法 JSON（仅输出 JSON，不要任何解释文字）。\n"
    "约束：\n"
    "1. retrieval_plans 必须恰好 3 条，type 分别为 core、distinctive、supporting，且每条 query 必须是英文短语义短语（供 FashionCLIP 检索）。\n"
    "2. request_signature.generic_tendencies_to_avoid 至少 1 条，用来防止推荐坍缩成日常基础款。\n"
    "3. candidate_requirements 六个品类配额之和不超过 60。\n"
    "4. score_weight 为 0 到 1 之间的数，core 最高。\n"
    "5. request_signature.explicit_style 单独保留用户明确指定的风格词"
    "（如“美拉德”“多巴胺”“薄荷曼波”），不要把它们模糊进 unique_mood。\n"
)

_AGENT2_SYSTEM = (
    "你是一个个人穿搭搭配组合师。你从给定的候选池中挑选单品，组成 3 到 5 套完整搭配。\n"
    "输出必须是合法 JSON（仅输出 JSON，不要任何解释文字）。\n"
    "硬约束：\n"
    "1. 每个 outfit 的 item_ids 必须全部来自候选池清单中的 item_id，绝不允许使用池外的 ID（池外 ID 会被系统丢弃）。\n"
    "2. 先策略后选品：先设计 composition_strategy（visual_anchor 主视觉锚点 / supporting_direction 支撑方向 / practical_balance 实穿平衡），再选单品。\n"
    "3. 避免与近期已推荐的结构签名（category_structure / dominant_color_family / style_mix / layer_count）高度重复。\n"
    "4. 至少保留 1 套更实穿的备选方案。\n"
    "5. style_tag 是自由文本摘要（如“剧场复古”），不是固定枚举。\n"
    "6. 每个 outfit 的 item_ids 不得有重复单品。\n"
)

_AGENT3_SYSTEM = (
    "你是一个穿搭方案评审与决策官。对给定搭配方案执行单次调用、双阶段评审协议，并输出最终决策。\n"
    "输出必须是合法 JSON（仅输出 JSON，不要任何解释文字）。\n"
    "阶段一（盲评）：只依据每套方案的单品数据（品类、颜色、风格标签、描述），不得参考 reasoning 文字来打分。\n"
    "阶段二（解释核对）：核对每套 reasoning 的主张是否有单品数据依据，得出 explanation_assessment.grounded 与 unsupported_claims。\n"
    "五个评审维度及权重：需求还原度 25%、请求特异性 25%、单品协调性 20%、实穿性 15%、新鲜感 15%（每维度 1-10 分）。\n"
    "决策枚举：\n"
    "- accept：方案达标，采纳。\n"
    "- recompose：候选池中有能承载主题的特色单品，但 Composer 没有使用（问题在组合）。feedback 说明。\n"
    "- retrieve_more：候选池本身缺少能承载主题的特色单品（问题在候选池）。feedback 说明。\n"
    "- wardrobe_gap：整个衣橱缺少必要品类（问题在衣橱）。附 missing_items（品类 + 期望特征）和 best_effort_outfit_id（最可行的方案）。\n"
)

_FEW_SHOT_AGENT1: dict[str, Any] = {
    "request_signature": {
        "theme": "看《悲惨世界》音乐剧",
        "explicit_style": [],
        "unique_mood": ["悲壮", "克制", "复古文学感"],
        "practical_context": ["剧场", "久坐", "半正式"],
        "generic_tendencies_to_avoid": [
            "仅由基础款组成，缺少能承载主题的视觉重点",
            "组合与普通通勤推荐几乎无差异",
            "只通过理由解释主题，单品本身没有体现",
        ],
    },
    "retrieval_plans": [
        {"type": "core", "query": "dark vintage literary theater outfit", "score_weight": 0.40},
        {"type": "distinctive", "query": "structured burgundy charcoal statement piece", "score_weight": 0.30},
        {"type": "supporting", "query": "comfortable semi-formal old-world texture", "score_weight": 0.10},
    ],
    "candidate_requirements": {"tops": 10, "bottoms": 10, "dresses": 6, "outerwear": 8, "shoes": 8, "accessories": 8},
}

_FEW_SHOT_AGENT2: dict[str, Any] = {
    "outfits": [
        {
            "outfit_id": "outfit_001",
            "composition_strategy": {
                "visual_anchor": "深酒红或炭灰结构感外套",
                "supporting_direction": "克制的浅色内搭与深色下装",
                "practical_balance": "保证剧场久坐舒适，避免过度戏服化",
            },
            "item_ids": ["coat_07", "top_03", "bottom_05", "shoes_02"],
            "style_tag": "剧场复古",
            "reasoning": "炭灰色大衣提供结构化轮廓，配合深酒红内搭体现悲壮感",
            "request_specific_elements": [
                {"item_id": "coat_07", "role": "通过结构感和深色调体现剧场仪式感"}
            ],
        }
    ]
}

_FEW_SHOT_AGENT3: dict[str, Any] = {
    "outfit_assessment": {
        "outfit_id": "outfit_002",
        "dimension_scores": {
            "request_relevance": 9,
            "request_specificity": 8,
            "outfit_coordination": 9,
            "wearability": 8,
            "freshness": 7,
        },
        "reasoning": "通过深色结构感单品表达悲壮氛围，同时保证了实穿性",
        "improvements": "配饰上可增加一枚胸针强化主题",
    },
    "explanation_assessment": {"grounded": True, "unsupported_claims": []},
    "alternatives": [
        {"outfit_id": "outfit_001", "strength": "更实穿"},
        {"outfit_id": "outfit_003", "strength": "更有主题表达"},
    ],
    "decision": "accept",
    "failure_source": "",
    "feedback": "",
    "missing_items": [],
    "best_effort_outfit_id": "",
}


def _json_example(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_agent1_prompt(
    *,
    user_query: str,
    wardrobe_summary: dict[str, Any],
    recent_memories: list[dict[str, Any]],
    weights: dict[str, float] | None = None,
) -> tuple[str, str]:
    resolved = normalize_weights(weights)
    rubric_note = (
        "你为五维统一评价标准准备检索信息和候选：\n"
        "- 需求还原度 → core 检索为主方向\n"
        "- 请求特异性 → distinctive 检索 + unique_mood\n"
        "- 搭配协调性 → supporting 检索为组合提供兼容单品\n"
        "- 实穿性 → practical_context\n"
        "- 新鲜感 → 参考近期请求记忆避免重复\n"
    )
    system = _AGENT1_SYSTEM + "\n" + rubric_text(resolved) + "\n" + rubric_note
    user = (
        "【用户请求】\n"
        f"{user_query}\n\n"
        "【衣橱统计摘要（只有数量与样例，不是全部单品）】\n"
        f"{json.dumps(wardrobe_summary, ensure_ascii=False)}\n\n"
        "【近期请求记忆（最近5次的 request_signature，用于避免相似化）】\n"
        + (json.dumps(recent_memories, ensure_ascii=False) if recent_memories else "（暂无）")
        + "\n\n【输出 JSON 示例】\n"
        + _json_example(_FEW_SHOT_AGENT1)
    )
    return system, user


def build_agent2_prompt(
    *,
    user_query: str,
    request_signature: dict[str, Any],
    pool_manifest: list[dict[str, Any]],
    recent_structure_signatures: list[dict[str, Any]],
    weights: dict[str, float] | None = None,
) -> tuple[str, str]:
    resolved = normalize_weights(weights)
    system = (
        _AGENT2_SYSTEM
        + "\n"
        + rubric_text(resolved)
        + "\n五维是组合取舍的决策目标（优先满足权重最高的维度），"
        "不要求你输出五维评分，只输出搭配方案。"
    )
    user = (
        "【用户请求】\n"
        f"{user_query}\n\n"
        "【请求画像 request_signature】\n"
        f"{json.dumps(request_signature, ensure_ascii=False)}\n\n"
        "【候选池清单（Top-50，只能从这里选 item_id）】\n"
        f"{json.dumps(pool_manifest, ensure_ascii=False)}\n\n"
        "【近期推荐结构签名（避免重复）】\n"
        + (json.dumps(recent_structure_signatures, ensure_ascii=False) if recent_structure_signatures else "（暂无）")
        + "\n\n【输出 JSON 示例】\n"
        + _json_example(_FEW_SHOT_AGENT2)
    )
    return system, user


def build_agent3_prompt(
    *,
    user_query: str,
    request_signature: dict[str, Any],
    outfits: list[dict[str, Any]],
    weights: dict[str, float] | None = None,
) -> tuple[str, str]:
    resolved = normalize_weights(weights)
    system = _AGENT3_SYSTEM + "\n" + rubric_text(resolved)
    user = (
        "【用户请求】\n"
        f"{user_query}\n\n"
        "【请求画像 request_signature】\n"
        f"{json.dumps(request_signature, ensure_ascii=False)}\n\n"
        "【候选搭配方案（3-5 套。请先盲评单品数据，再核对 reasoning）】\n"
        f"{json.dumps(outfits, ensure_ascii=False)}\n\n"
        "【输出 JSON 示例】\n"
        + _json_example(_FEW_SHOT_AGENT3)
    )
    return system, user
