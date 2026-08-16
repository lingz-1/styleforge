"""Strict prompts for the five extension tasks handled by the shared agents."""

from __future__ import annotations

import json
from typing import Any

from styleforge.models.agent_tasks import (
    Agent1TaskOutput,
    Agent2TaskOutput,
    ExtensionIntentEnrichment,
    ExtensionReview,
)
from styleforge.models.context import ContextPack
from styleforge.models.task_results import RESULT_MODELS
from styleforge.orchestration.task_router import TaskType


EXTENSION_PROMPT_VERSION = "extension-three-agent-v3.2"

TASK_COMPLETION_RULES: dict[TaskType, str] = {
    TaskType.OUTFIT_MODIFY: (
        "completed 时 alternatives 至少一套；每套必须包含所有 locked_item_ids，"
        "移除 replaced_item_ids，且只使用 replacement_item_ids 替换目标槽位；"
        "优先依据 Agent 1 facts.candidate_item_texts（含单品名称与类型）挑选替换单品，"
        "用户指名具体单品或类型时（如\"帽子\"），必须选名称/类型匹配该词的候选，"
        "不得用同槽位的其他单品替代。"
    ),
    TaskType.STYLE_ADVICE: (
        "completed 时 principles 至少一条，并说明如何用 wardrobe_matches 落地；"
        "没有指定场合不属于阻塞信息，应给通用风格建议。"
    ),
    TaskType.ITEM_ADVICE: (
        "只要 Agent 1 已解析 anchor_item，就不得因缺少场合、季节或偏好返回 needs_clarification。"
        "completed 时必须原样保留 anchor_item，compatible_items_by_slot 至少一个分组非空，"
        "sample_outfits 至少一套；每套 item_ids 必须包含锚点和衣橱支撑单品，并提供 reasoning。"
        "只能引用 Agent 1 candidate_item_ids 中出现的单品 ID；"
        "严禁联想或补全同系列、同款不同色等候选范围外单品，"
        "候选支撑不足时宁可减少搭配套数或分组数量，也绝不引用范围外 ID。"
    ),
    TaskType.WARDROBE_COMPATIBILITY: (
        "只要候选新品已解析，就不得因缺少场合返回 needs_clarification。"
        "complete_outfit_count 大于零时必须给出 sample_outfits。"
    ),
    TaskType.WARDROBE_GAP: (
        "general 和 targeted 都是完整输入，不得仅因缺少场合返回 needs_clarification；"
        "covered_elements 可从 Agent 1 facts.covered_elements 中选取最相关的覆盖证据，但不得新增；"
        "gaps 必须逐项对应 Agent 1 facts.missing_elements；gap_count 必须等于 gaps 数量。"
        "如果 missing_elements 为空，gap_count=0 是有效结论，summary 必须明确说明当前目标元素均已覆盖。"
    ),
}


_FLEXIBLE_ADJUST_RULE = (
    "completed 时 alternatives 至少 3 套，每套风格/单品组合应有差异，且每套是一套完整可穿搭配；"
    "target_slot 为空、无强制锁定单品；每套只可从 Agent 1 candidate_item_ids 中选择单品，"
    "至少更换或新增 1 件；整套朝向用户请求的正式度/颜色/风格方向调整；"
    "每套中鞋/下装/外套/连衣裙等核心槽位各至多 1 件，不得出现两双鞋、"
    "两条下装或裤+裙同穿等重复核心槽位单品；上衣可叠穿多件，"
    "耳饰/戒指/手链/项链等配饰可自由叠加，但帽子/包/眼镜/腰带等非叠加类型只能各 1 件；"
    "若 Agent 1 facts 指定了 required_slot（当前搭配缺少该槽位），"
    "每套必须从 required_slot_item_ids 中选择至少 1 件补齐该槽位；"
    "优先依据 Agent 1 facts.candidate_item_texts（含单品名称与类型）挑选单品，"
    "当用户指名具体单品或类型（如\"帽子\"\"项链\"）时，必须从名称/类型匹配该词的候选中选择，"
    "不得用同槽位的其他单品替代（例如要帽子不能给戒指）；"
    "若衣橱无法满足调整方向或缺少 required_slot 单品则返回 infeasible。"
)


def completion_rule_for(agent1_output: Agent1TaskOutput) -> str:
    """Return the task-completion rule, branching for flexible-adjust mode."""
    if agent1_output.task_type is TaskType.OUTFIT_MODIFY and agent1_output.facts.get(
        "adjustment_mode"
    ) == "flexible":
        return _FLEXIBLE_ADJUST_RULE
    return TASK_COMPLETION_RULES[agent1_output.task_type]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def build_extension_agent1_prompt(
    *,
    request: str,
    context_pack: ContextPack,
    tool_output: Agent1TaskOutput,
) -> tuple[str, str]:
    system = (
        "你是 StyleForge 的 Agent 1（语义检索与意图理解）。"
        "你必须基于给定 Context Pack 和工具事实理解用户意图；工具事实是权威数据，"
        "不得虚构衣橱单品、知识来源或场景。"
        "事实工具的 needs_clarification 是阻塞信息的唯一依据；场合、季节、更多偏好只属于可选上下文。"
        "只输出符合 JSON Schema 的对象。"
    )
    user = (
        f"提示词版本：{EXTENSION_PROMPT_VERSION}\n"
        f"用户请求：{request}\n"
        f"Context Pack：\n{_json(context_pack.model_dump(mode='json'))}\n"
        f"事实工具输出：\n{_json(tool_output.model_dump(mode='json'))}\n"
        "请补充意图摘要、目标风格/场合/单品词，以及可提升个性化但不阻塞任务的 optional_context。"
        "不要改写事实工具中的 ID、候选范围和硬约束。\n"
        f"输出 Schema：\n{_json(ExtensionIntentEnrichment.model_json_schema())}"
    )
    return system, user


def build_extension_agent2_prompt(
    *,
    request: str,
    context_pack: ContextPack,
    agent1_output: Agent1TaskOutput,
    critic_feedback: str = "",
    repair_feedback: str = "",
) -> tuple[str, str]:
    task_type = agent1_output.task_type
    result_model = RESULT_MODELS[task_type]
    system = (
        "你是 StyleForge 的 Agent 2（造型方案生成）。"
        "你必须根据 Agent 1 的意图、事实和候选范围完成当前业务任务。"
        "只可引用 candidate_item_ids、事实中的衣橱单品 ID 和 evidence.source_id；"
        "不得虚构商品、品牌、链接、衣橱内容或知识来源。"
        "只有 Agent 1 明确 needs_clarification=true 时才允许输出 needs_clarification。"
        "可选场合或偏好缺失时，必须基于现有衣橱给出通用方案。只输出 JSON。"
    )
    if (
        agent1_output.task_type is TaskType.OUTFIT_MODIFY
        and agent1_output.facts.get("adjustment_mode") == "flexible"
    ):
        # Flexible rebuilds are where the LLM most often packs a duplicated core
        # slot into one outfit. State the rule at system level so it is followed
        # up front instead of relying on a post-hoc repair pass.
        system += (
            "\n\n硬性搭配约束（每套备选方案都必须逐条满足，违反任一条件即整轮失败）："
            "\n- 核心槽位（下装/鞋/连衣裙/外套）在每套中至多出现 1 件；"
            "不得出现两双鞋、两条下装、或裤+裙同穿等重复。"
            "\n- 非可叠加单品类型（帽子/包/眼镜/腰带等）每套各至多 1 件。"
            "\n- 上衣可叠穿多件；耳饰/戒指/手链/项链等配饰可自由叠加。"
            "\n- 备选方案至少 3 套，每套都是一套完整可穿搭配，且各套之间应有差异。"
            "\n- 每套单品只能从 Agent 1 candidate_item_ids 中选择，不得重复同一单品，"
            "也不得从其他任务复用与当前请求无关的单品。"
        )
    user = (
        f"提示词版本：{EXTENSION_PROMPT_VERSION}\n"
        f"任务类型：{task_type.value}\n"
        f"用户请求：{request}\n"
        f"Context Pack：\n{_json(context_pack.model_dump(mode='json'))}\n"
        f"Agent 1 输出：\n{_json(agent1_output.model_dump(mode='json'))}\n"
        f"当前任务完成条件：{completion_rule_for(agent1_output)}\n"
        f"Agent 3 上轮反馈：{critic_feedback or '无，这是首次生成'}\n"
        f"结构修复反馈：{repair_feedback or '无'}\n"
        "顶层必须符合 Agent2TaskOutput；result.status 必须与顶层 status 一致。"
        "used_item_ids 列出 result 实际引用的全部衣橱 ID，"
        "evidence_source_ids 列出使用的证据 source_id。\n"
        f"顶层 Schema：\n{_json(Agent2TaskOutput.model_json_schema())}\n"
        f"result Schema：\n{_json(result_model.model_json_schema())}"
    )
    return system, user


def build_extension_agent3_prompt(
    *,
    request: str,
    context_pack: ContextPack,
    agent1_output: Agent1TaskOutput,
    agent2_output: Agent2TaskOutput,
    hard_checks: list[str],
) -> tuple[str, str]:
    system = (
        "你是 StyleForge 的 Agent 3（审校与最终决策）。"
        "你必须独立检查方案是否回答用户请求、是否由 Agent 1 事实支撑、"
        "是否尊重锁定/候选/衣橱约束。硬校验已经通过，但不能替代你的语义审校。"
        "Agent 1 的职责是解释意图、检索事实和限定候选范围，不负责直接给出最终建议；"
        "不得以 Agent 1 没有直接回答用户为由拒绝 Agent 2。"
        "用户历史请求只可作为相关时的可选个性化信息；不得因为方案没有利用与当前请求无关的历史偏好而拒绝。"
        "对于衣橱缺口任务，facts.covered_elements 是现有覆盖证据，candidate_item_ids 引用这些现有单品是正确行为；"
        "facts.missing_elements 才是缺口的唯一事实范围。missing_elements 为空时，零缺口是有效答案，"
        "不得仅因用户问了‘缺什么’就要求虚构缺口。"
        "发现实质问题时 approved=false；不得自行生成替代方案。只输出 JSON。"
    )
    user = (
        f"提示词版本：{EXTENSION_PROMPT_VERSION}\n"
        f"用户请求：{request}\n"
        f"Context Pack：\n{_json(context_pack.model_dump(mode='json'))}\n"
        f"Agent 1 输出：\n{_json(agent1_output.model_dump(mode='json'))}\n"
        f"Agent 2 输出：\n{_json(agent2_output.model_dump(mode='json'))}\n"
        f"当前任务完成条件：{completion_rule_for(agent1_output)}\n"
        f"已通过的硬校验：\n{_json(hard_checks)}\n"
        f"输出 Schema：\n{_json(ExtensionReview.model_json_schema())}"
    )
    return system, user
