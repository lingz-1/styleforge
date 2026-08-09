"""Hard validators for Agent 2 extension drafts.

These checks enforce data boundaries only. They never create or repair advice.
"""

from __future__ import annotations

from typing import Any

from styleforge.models.agent_tasks import Agent1TaskOutput, Agent2TaskOutput
from styleforge.orchestration.task_router import TaskType


def _item_ids(items: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("item_id", "")) for item in items if item.get("item_id")}


def _grouped_item_ids(grouped: dict[str, list[dict[str, Any]]]) -> set[str]:
    return {item_id for items in grouped.values() for item_id in _item_ids(items)}


def _validate_used_ids(
    agent1: Agent1TaskOutput,
    agent2: Agent2TaskOutput,
    wardrobe_ids: set[str],
) -> list[str]:
    issues: list[str] = []
    used_ids = set(agent2.used_item_ids)
    if not used_ids <= wardrobe_ids:
        issues.append(f"结果引用了非当前衣橱 ID: {sorted(used_ids - wardrobe_ids)[:5]}")
    allowed_ids = set(agent1.candidate_item_ids)
    allowed_ids.update(agent1.facts.get("current_item_ids", []))
    if used_ids and not used_ids <= allowed_ids:
        issues.append(f"结果引用了 Agent 1 候选范围外 ID: {sorted(used_ids - allowed_ids)[:5]}")
    evidence_ids = {item.source_id for item in agent1.evidence}
    used_evidence = set(agent2.evidence_source_ids)
    if not used_evidence <= evidence_ids:
        issues.append(f"结果引用了未检索证据: {sorted(used_evidence - evidence_ids)[:5]}")
    return issues


def _validate_modify(agent1: Agent1TaskOutput, result: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    locked = set(agent1.facts.get("locked_item_ids", []))
    replaced = set(agent1.facts.get("replaced_item_ids", []))
    replacements = set(agent1.facts.get("replacement_item_ids", []))
    if set(result.get("locked_item_ids", [])) != locked:
        issues.append("局部修改结果未原样保留全部锁定单品")
    if set(result.get("replaced_item_ids", [])) != replaced:
        issues.append("局部修改结果的被替换单品与 Agent 1 事实不一致")
    for alternative in result.get("alternatives", []):
        item_ids = set(alternative.get("item_ids", []))
        if not locked <= item_ids:
            issues.append("局部修改备选方案丢失锁定单品")
        if replaced & item_ids:
            issues.append("局部修改备选方案仍包含被替换单品")
        if not item_ids - locked <= replacements:
            issues.append("局部修改备选方案包含候选替换范围外单品")
    return issues


def _validate_item_advice(agent1: Agent1TaskOutput, result: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    facts = agent1.facts
    expected_anchor = facts.get("anchor_item") or {}
    actual_anchor = result.get("anchor_item") or {}
    if expected_anchor and actual_anchor.get("item_id") != expected_anchor.get("item_id"):
        issues.append("单品建议结果更换了 Agent 1 已解析的锚点单品")
    referenced = _grouped_item_ids(result.get("compatible_items_by_slot", {}))
    referenced.update(_item_ids(result.get("wardrobe_matches", [])))
    referenced.update(_grouped_item_ids(result.get("wardrobe_matches_by_slot", {})))
    for outfit in result.get("sample_outfits", []):
        outfit_ids = set(outfit.get("item_ids", outfit.get("wardrobe_item_ids", [])))
        referenced.update(outfit_ids)
        if expected_anchor and expected_anchor.get("item_id") not in outfit_ids:
            issues.append("单品建议示例搭配没有保留锚点单品")
    if result.get("anchor_source") == "candidate" and expected_anchor:
        referenced.discard(str(expected_anchor.get("item_id", "")))
    if not referenced <= set(agent1.candidate_item_ids):
        issues.append("单品建议引用了 Agent 1 候选范围外单品")
    return issues


def _validate_compatibility(result: dict[str, Any], wardrobe_ids: set[str]) -> list[str]:
    issues: list[str] = []
    candidate_id = str((result.get("candidate_item") or {}).get("item_id", ""))
    if candidate_id and candidate_id in wardrobe_ids:
        issues.append("兼容性候选新品被错误当作已有衣橱单品")
    referenced = _grouped_item_ids(result.get("compatible_items_by_slot", {}))
    referenced.update(_item_ids(result.get("similar_wardrobe_items", [])))
    for outfit in result.get("sample_outfits", []):
        referenced.update(outfit.get("wardrobe_item_ids", []))
    if not referenced <= wardrobe_ids:
        issues.append("兼容性结果引用了当前衣橱外单品")
    return issues


def _element_ids(elements: list[dict[str, Any]]) -> set[str]:
    return {str(element.get("id", "")) for element in elements if element.get("id")}


def _validate_gap(
    agent1: Agent1TaskOutput,
    result: dict[str, Any],
    wardrobe_ids: set[str],
) -> list[str]:
    issues: list[str] = []
    facts = agent1.facts
    for element in result.get("covered_elements", []):
        if not set(element.get("matching_item_ids", [])) <= wardrobe_ids:
            issues.append("衣橱缺口的覆盖证据包含当前衣橱外单品")
    expected_covered = _element_ids(facts.get("covered_elements", []))
    actual_covered = _element_ids(result.get("covered_elements", []))
    if not actual_covered <= expected_covered:
        issues.append("衣橱缺口结果包含 Agent 1 未确认的已覆盖元素")
    expected_missing = _element_ids(facts.get("missing_elements", []))
    actual_missing = _element_ids(result.get("gaps", []))
    if actual_missing != expected_missing:
        issues.append("衣橱缺口清单与 Agent 1 的 missing_elements 不一致")
    if result.get("wardrobe_item_count") != facts.get("wardrobe_item_count"):
        issues.append("衣橱缺口结果的衣物总数与 Agent 1 事实不一致")
    if result.get("slot_counts") != facts.get("slot_counts"):
        issues.append("衣橱缺口结果的槽位统计与 Agent 1 事实不一致")
    serialized = str(result).lower()
    if "http://" in serialized or "https://" in serialized:
        issues.append("衣橱缺口结果包含不允许的商品或品牌链接")
    return issues


def validate_extension_draft(
    *,
    agent1: Agent1TaskOutput,
    agent2: Agent2TaskOutput,
    wardrobe_ids: set[str],
) -> list[str]:
    """Return passed check labels or raise when a hard boundary is violated."""
    issues = _validate_used_ids(agent1, agent2, wardrobe_ids)
    result = agent2.result
    if agent1.task_type is TaskType.OUTFIT_MODIFY:
        issues.extend(_validate_modify(agent1, result))
    elif agent1.task_type is TaskType.ITEM_ADVICE:
        issues.extend(_validate_item_advice(agent1, result))
    elif agent1.task_type is TaskType.WARDROBE_COMPATIBILITY:
        issues.extend(_validate_compatibility(result, wardrobe_ids))
    elif agent1.task_type is TaskType.WARDROBE_GAP:
        issues.extend(_validate_gap(agent1, result, wardrobe_ids))
    if issues:
        raise ValueError("；".join(dict.fromkeys(issues)))
    return [
        "全部引用 ID 均通过衣橱与 Agent 1 候选边界校验",
        "证据引用通过 Agent 1 检索来源校验",
        "任务专属硬约束校验通过",
    ]
