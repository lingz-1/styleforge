"""Prompt builders for the three semantic agents.

The builders return ``(system, user)`` message pairs. All retrieval query
strings are English short phrases because they feed the English FashionCLIP
model. Few-shot examples mirror the frozen v3.2.1-final spec plus the
V2.1 weather contract (implicit intent, location priority, environment
adjustments, carry recommendations and environment assessment).
"""

from __future__ import annotations

import json
from typing import Any

from styleforge.core.rubric import normalize_weights, rubric_text

PROMPT_VERSION = "2026.08.10-weather-v2.1"


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
    "阶段一（盲评）：对候选中的【每一套】方案都给出 dimension_scores（五维各 1-10 分）。"
    "只依据每套方案的单品数据（品类、颜色、风格标签、描述），不得参考 reasoning 文字来打分。"
    "outfit_assessment 用于你最终认可的首选方案，alternatives 中每个备选方案也必须带自己的 dimension_scores。\n"
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
        "practical_context": ["剧场", "久坐", "半正式", "晚间散场体感下降"],
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
    "context_requirements": {
        "temporal": {"needed": True, "expression": "明天", "reason": "需要确定演出日期的天气"},
        "location": {"needed": True, "query": "上海", "allow_profile_default": True, "reason": "天气查询需要地点"},
        "weather": {
            "needed": True,
            "location": "上海",
            "date": "明天",
            "granularity": "daily",
            "reason": "晚间散场与通勤受降水和温度影响",
        },
    },
    "implicit_context_signals": ["用户未显式提天气，但剧场出行隐含日期、地点和温度依赖"],
    "context_criticality": "helpful",
    "uncertainties": [],
    "default_policy_allowed": True,
}

_FEW_SHOT_AGENT1_NEGATIVE: dict[str, Any] = {
    "request_signature": {
        "theme": "黑色马甲怎么搭",
        "explicit_style": [],
        "unique_mood": ["干练", "简约"],
        "practical_context": [],
        "generic_tendencies_to_avoid": [
            "仅由基础款组成，缺少能承载主题的视觉重点",
        ],
    },
    "retrieval_plans": [
        {"type": "core", "query": "black vest layering outfit", "score_weight": 0.40},
        {"type": "distinctive", "query": "minimal chic black waistcoat styling", "score_weight": 0.30},
        {"type": "supporting", "query": "versatile neutral tops and bottoms", "score_weight": 0.10},
    ],
    "candidate_requirements": {"tops": 12, "bottoms": 12, "dresses": 0, "outerwear": 0, "shoes": 8, "accessories": 0},
    "context_requirements": {
        "temporal": {"needed": False, "expression": "", "reason": ""},
        "location": {"needed": False, "query": "", "allow_profile_default": True, "reason": ""},
        "weather": {"needed": False, "location": "", "date": "", "granularity": "daily", "reason": ""},
    },
    "implicit_context_signals": [],
    "context_criticality": "not_needed",
    "uncertainties": [],
    "default_policy_allowed": False,
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
            "environment_adjustments": [
                {
                    "fact_refs": ["weather:day:2026-08-11:precipitation"],
                    "impact": "返程时段降水概率较高",
                    "action": "加入可脱卸的防水外层",
                    "wardrobe_item_ids": ["coat_07"],
                }
            ],
            "carry_recommendations": [
                {
                    "name": "折叠伞",
                    "reason": "散场返程时段可能有阵雨",
                    "fact_refs": ["weather:day:2026-08-11:precipitation"],
                    "category": "external_carry_item",
                }
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
    "environment_assessment": {
        "grounded": True,
        "coverage_complete": True,
        "unsupported_claims": [],
        "missing_adjustments": [],
        "carry_advice_grounded": True,
    },
    "alternatives": [
        {
            "outfit_id": "outfit_001",
            "strength": "更实穿",
            "dimension_scores": {
                "request_relevance": 8,
                "request_specificity": 7,
                "outfit_coordination": 8,
                "wearability": 9,
                "freshness": 6,
            },
        },
        {
            "outfit_id": "outfit_003",
            "strength": "更有主题表达",
            "dimension_scores": {
                "request_relevance": 9,
                "request_specificity": 9,
                "outfit_coordination": 7,
                "wearability": 6,
                "freshness": 8,
            },
        },
    ],
    "decision": "accept",
    "failure_source": "",
    "feedback": "",
    "missing_items": [],
    "best_effort_outfit_id": "",
}


def _json_example(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


_CONTEXT_CONTRACT = (
    "\n上下文需求规则：\n"
    "- 必须输出 context_requirements（temporal/location/weather）以及 implicit_context_signals、"
    "context_criticality、uncertainties、default_policy_allowed。\n"
    "- 隐含环境需求判定：即使用户没有出现“天气”二字，只要请求涉及出行、日期、地点、户外活动、时段，"
    "或天气/环境会实质影响穿着安全、舒适度、活动完成度或随身准备，就必须设置 weather.needed=true。示例：\n"
    "  * “明天穿什么”“明天上班怎么穿” → needed=true（隐含明天 + 当前所在位置的天气）\n"
    "  * “去北京旅游该怎么穿” → needed=true（隐含北京 + 近几天天气）\n"
    "  * “周末户外婚礼穿什么”“今晚露台约会穿什么”“明早骑车” → needed=true\n"
    "- 不误触：纯风格/单品知识问题（“黑色马甲怎么搭”“美拉德风格是什么”），且没有出行、时间、地点或实穿环境时，"
    "weather.needed=false 且 context_criticality=not_needed。\n"
    "- needed=true 时：\n"
    "  * location.query 填显式目的地（如“北京”）；本地日常请求（“明天穿什么”）留空并保留 allow_profile_default=true，"
    "由系统优先使用设备定位、再回退用户默认城市。\n"
    "  * temporal.expression 只填 今天/明天/YYYY-MM-DD；用户没给日期就留空，系统默认查询近 3 天窗口。\n"
    "  * weather.date 与 weather.location 作为向后兼容字段，与 temporal/location 保持一致。\n"
    "  * weather.granularity 默认 daily。\n"
    "  * context_criticality：天气对安全或决策必需（如极端天气、长期户外）→ required；只是有益增强 → helpful。\n"
    "- 天气工具只返回事实，最终穿搭判断仍由三个 Agent 完成。\n"
)


def build_agent1_prompt(
    *,
    user_query: str,
    wardrobe_summary: dict[str, Any],
    recent_memories: list[dict[str, Any]],
    weights: dict[str, float] | None = None,
    environment_context: dict[str, Any] | None = None,
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
    context_contract = _CONTEXT_CONTRACT
    examples = _json_example(_FEW_SHOT_AGENT1)
    if environment_context:
        context_contract += (
            "- 已提供工具返回的环境事实。只能据此调整 practical_context 和英文检索短语，"
            "不得编造缺失字段，也不得把工具事实当成穿搭结论；若 status=unavailable，则不写天气主张。\n"
        )
        examples += (
            "\n（同一请求拿到事实后的第二次执行：context_requirements 保持第一次的值，"
            "并把关键事实落到 practical_context 与检索短语。）"
        )
    system = (
        _AGENT1_SYSTEM
        + "\n"
        + rubric_text(resolved)
        + "\n"
        + rubric_note
        + context_contract
    )
    user = (
        "【用户请求】\n"
        f"{user_query}\n\n"
        "【衣橱统计摘要（只有数量与样例，不是全部单品）】\n"
        f"{json.dumps(wardrobe_summary, ensure_ascii=False)}\n\n"
        "【近期请求记忆（最近5次的 request_signature，用于避免相似化）】\n"
        + (json.dumps(recent_memories, ensure_ascii=False) if recent_memories else "（暂无）")
        + "\n\n【已获取的环境事实】\n"
        + (
            json.dumps(environment_context, ensure_ascii=False)
            if environment_context
            else "（尚未获取；请先声明是否需要）"
        )
        + "\n\n【输出 JSON 正例（需要环境上下文）】\n"
        + examples
        + "\n\n【输出 JSON 负例（纯风格知识，不触发天气）】\n"
        + _json_example(_FEW_SHOT_AGENT1_NEGATIVE)
    )
    return system, user


def build_agent2_prompt(
    *,
    user_query: str,
    request_signature: dict[str, Any],
    pool_manifest: list[dict[str, Any]],
    recent_structure_signatures: list[dict[str, Any]],
    weights: dict[str, float] | None = None,
    environment_context: dict[str, Any] | None = None,
) -> tuple[str, str]:
    resolved = normalize_weights(weights)
    system = (
        _AGENT2_SYSTEM
        + "\n"
        + rubric_text(resolved)
        + "\n五维是组合取舍的决策目标（优先满足权重最高的维度），"
        "不要求你输出五维评分，只输出搭配方案。"
    )
    if environment_context:
        system += (
            "\n天气/环境上下文是外部事实，不是穿搭结论。请在候选池范围内据此处理层次、材质、鞋履和实穿平衡，"
            "不得编造天气字段或池外单品；如果 status=unavailable，则忽略天气并且不要生成天气主张。\n"
            "当环境事实可用时，每个 outfit 必须提供 environment_adjustments 与 carry_recommendations：\n"
            "- environment_adjustments 每条包含 fact_refs（形如 weather:day:2026-08-11:precipitation，"
            "引用给出的环境事实键）、impact（天气如何影响）和 action（具体穿搭动作）；"
            "wardrobe_item_ids 只能引用本 outfit 的 item_ids 中的单品。\n"
            "- carry_recommendations 是外部随身物品（伞、水、墨镜、遮阳帽、发圈、花露水等），"
            "category 必须固定为 external_carry_item，绝不混入 item_ids；"
            "只有相关事实和活动支持时才建议，不要机械地每次全列。\n"
            "- 极端天气时允许输出安全提示或建议调整活动，不要把风险包装成“换套衣服即可”。"
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
        + "\n\n【环境事实】\n"
        + (
            json.dumps(environment_context, ensure_ascii=False)
            if environment_context
            else "（无）"
        )
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
    environment_context: dict[str, Any] | None = None,
) -> tuple[str, str]:
    resolved = normalize_weights(weights)
    system = _AGENT3_SYSTEM + "\n" + rubric_text(resolved)
    if environment_context:
        system += (
            "\n请把给定天气事实纳入实穿性审校，尤其检查温度、降水和风；"
            "不得用未提供的天气信息支持结论；如果 status=unavailable，则不做天气相关评价。\n"
            "当环境事实可用时，额外输出 environment_assessment 并逐条核对：\n"
            "1) 每条天气/环境主张必须有 fact_refs 且引用存在、状态可用、时间地点对应本次活动。\n"
            "2) environment_adjustments 引用的衣物必须属于本方案 item_ids 与候选池。\n"
            "3) carry_recommendations 必须 category=external_carry_item 且由事实和活动合理支持。\n"
            "4) status=unavailable 时仍出现“下雨/高温/UV/带伞”等断言 → grounded=false，列入 unsupported_claims。\n"
            "5) 多日/多城市请求遗漏关键时段 → coverage_complete=false，列入 missing_adjustments。\n"
            "6) 远期气候参考不得写成精确预报。\n"
            "环境审校只是决策的一部分：可在 feedback 指出可修正项；缺少必要环境事实或极端天气安全无法满足时，"
            "在 feedback 说明，不要循环把风险包装成穿搭建议。"
        )
    user = (
        "【用户请求】\n"
        f"{user_query}\n\n"
        "【请求画像 request_signature】\n"
        f"{json.dumps(request_signature, ensure_ascii=False)}\n\n"
        "【候选搭配方案（3-5 套。请先盲评单品数据，再核对 reasoning）】\n"
        f"{json.dumps(outfits, ensure_ascii=False)}\n\n"
        "【环境事实】\n"
        + (
            json.dumps(environment_context, ensure_ascii=False)
            if environment_context
            else "（无）"
        )
        + "\n\n"
        "【输出 JSON 示例】\n"
        + _json_example(_FEW_SHOT_AGENT3)
    )
    return system, user
