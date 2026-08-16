"""Hard validators for Agent 2 extension drafts.

These checks enforce data boundaries only. They never create or repair advice.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from styleforge.core.categories import infer_slot
from styleforge.models.agent_tasks import Agent1TaskOutput, Agent2TaskOutput
from styleforge.orchestration.task_router import TaskType

# Dressing-core slots that must appear at most once per outfit, even across
# sub-types: a pair of trousers plus a skirt (both bottom) is as wrong as two
# pairs of shoes. Accessory slots are not here — a hat plus a ring stays legal.
_CORE_SLOTS = {"bottom", "footwear", "one_piece", "outerwear"}

# Item types that may legitimately repeat: layered tops (shirt + cardigan) and
# stacked jewellery (earrings + rings + bracelet). Everything else is one per
# outfit — two hats or two bags never pass.
_STACKABLE_TYPES = {
    "top",
    "earrings",
    "rings",
    "bracelet",
    "necklace",
    "jewellery",
    "brooch",
}

_MIN_FLEXIBLE_ALTERNATIVES = 3


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


def _validate_flexible(agent1: Agent1TaskOutput, result: dict[str, Any]) -> list[str]:
    """Data-boundary checks for free-form rebuilds (overall / missing-slot).

    The replaced and locked sets are the LLM's own choice in flexible mode, so
    the single-slot equality checks below do not apply. What must still hold:
    every alternative stays inside the candidate scope, actually changes the
    outfit, and (when a missing slot was requested) gains that slot.
    """
    issues: list[str] = []
    current = set(agent1.facts.get("current_item_ids", []))
    allowed = set(agent1.candidate_item_ids) | current
    required_ids = set(agent1.facts.get("required_slot_item_ids", []))
    required_slot = str(agent1.facts.get("required_slot", ""))
    type_map = {
        **dict(agent1.facts.get("candidate_item_types") or {}),
        **{
            card["item_id"]: card["item_type"]
            for card in agent1.facts.get("current_item_texts") or []
        },
    }
    alternatives = result.get("alternatives", [])
    if len(alternatives) < _MIN_FLEXIBLE_ALTERNATIVES:
        issues.append(
            f"整体调整备选方案至少需要 {_MIN_FLEXIBLE_ALTERNATIVES} 套供选择"
        )
    for alternative in alternatives:
        item_ids = set(alternative.get("item_ids", []))
        if not item_ids <= allowed:
            issues.append("整体调整备选方案包含候选范围外单品")
        if item_ids == current:
            issues.append("整体调整备选方案未做任何调整")
        if required_ids and not (item_ids & required_ids):
            issues.append(f"整体调整备选方案缺少目标槽位 {required_slot} 的单品")
        # Reject duplicate core slots and duplicate non-stackable item types.
        # Two pairs of shoes fail on the slot; two hats fail on the item type;
        # a layered shirt + cardigan (both top) and stacked earrings + rings
        # pass because their types are stackable. Named cards carry types;
        # untyped fallback candidates are skipped, not guessed.
        slot_counts: Counter = Counter()
        type_counts: Counter = Counter()
        for uid in item_ids:
            item_type = type_map.get(str(uid))
            if not item_type:
                continue
            slot_counts[infer_slot(item_type)] += 1
            type_counts[item_type] += 1
        dupes = sorted(slot for slot in _CORE_SLOTS if slot_counts[slot] > 1)
        if dupes:
            issues.append(f"备选方案重复核心槽位: {dupes}")
        dup_types = sorted(
            item_type
            for item_type, count in type_counts.items()
            if count > 1 and item_type not in _STACKABLE_TYPES
        )
        if dup_types:
            issues.append(f"备选方案重复单品类型: {dup_types}")
    return issues


def _validate_modify(agent1: Agent1TaskOutput, result: dict[str, Any]) -> list[str]:
    if agent1.facts.get("adjustment_mode") == "flexible":
        return _validate_flexible(agent1, result)
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


def _filter_item_refs(
    items: list[dict[str, Any]],
    allowed_ids: set[str],
) -> list[dict[str, Any]]:
    return [item for item in items if str(item.get("item_id", "")) in allowed_ids]


def _filter_slot_refs(
    grouped: dict[str, list[dict[str, Any]]],
    allowed_ids: set[str],
) -> dict[str, list[dict[str, Any]]]:
    kept: dict[str, list[dict[str, Any]]] = {}
    for slot, items in grouped.items():
        filtered = _filter_item_refs(items, allowed_ids)
        if filtered:
            kept[slot] = filtered
    return kept


def _sanitize_item_advice_result(
    result: dict[str, Any],
    allowed_ids: set[str],
    anchor_id: str,
) -> dict[str, Any]:
    """Prune item references in an item-advice draft to the candidate scope.

    Out-of-scope items (LLM hallucinated same-series variants etc.) are removed
    from grouped suggestions, flat match lists and sample outfits. A sample
    outfit is kept only when it still carries the anchor plus at least one
    supporting item; anything emptied by pruning is dropped. Never invents or
    reorders items. The caller decides how to handle a contract violation that
    survives pruning (repair retry, then fail).
    """
    pruned = dict(result)
    if pruned.get("compatible_items_by_slot"):
        pruned["compatible_items_by_slot"] = _filter_slot_refs(
            pruned["compatible_items_by_slot"], allowed_ids
        )
    if pruned.get("wardrobe_matches"):
        pruned["wardrobe_matches"] = _filter_item_refs(
            pruned["wardrobe_matches"], allowed_ids
        )
    if pruned.get("wardrobe_matches_by_slot"):
        pruned["wardrobe_matches_by_slot"] = _filter_slot_refs(
            pruned["wardrobe_matches_by_slot"], allowed_ids
        )
    outfits: list[dict[str, Any]] = []
    for outfit in pruned.get("sample_outfits", []):
        kept_ids = [
            item_id for item_id in outfit.get("item_ids", []) if item_id in allowed_ids
        ]
        if anchor_id and anchor_id not in kept_ids:
            continue  # every sample outfit must keep the anchor
        if len(kept_ids) < 2:
            continue  # contract requires the anchor plus a supporting item
        copy = dict(outfit)
        copy["item_ids"] = kept_ids
        outfits.append(copy)
    pruned["sample_outfits"] = outfits
    return pruned


def _prune_flexible_duplicates(
    agent1: Agent1TaskOutput,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Drop duplicate core-slot and non-stackable items from a flexible draft.

    Pure cleanup for LLM drafts that repeat a core slot (two pairs of trousers)
    or a non-stackable type (two hats) inside one alternative. Layered tops and
    stacked jewellery keep their duplicates because those types are stackable.
    The first occurrence of each slot/type wins; items whose type is unknown are
    never guessed, so they stay untouched. Mirrors the counting rule in
    ``_validate_flexible`` so a pruned draft passes the hard validator.
    """
    type_map = {
        **dict(agent1.facts.get("candidate_item_types") or {}),
        **{
            card["item_id"]: card["item_type"]
            for card in agent1.facts.get("current_item_texts") or []
        },
    }
    alternatives = result.get("alternatives") or []
    pruned: list[dict[str, Any]] = []
    for alternative in alternatives:
        seen_slots: set[str] = set()
        seen_types: set[str] = set()
        kept: list[str] = []
        for uid in alternative.get("item_ids", []):
            item_type = type_map.get(str(uid))
            if not item_type:
                kept.append(uid)
                continue
            slot = infer_slot(item_type)
            if slot in _CORE_SLOTS and slot in seen_slots:
                continue
            if item_type not in _STACKABLE_TYPES and item_type in seen_types:
                continue
            kept.append(uid)
            seen_slots.add(slot)
            seen_types.add(item_type)
        if kept == alternative.get("item_ids", []):
            pruned.append(alternative)
        else:
            copy = dict(alternative)
            copy["item_ids"] = kept
            pruned.append(copy)
    if pruned == alternatives:
        return result
    return {**result, "alternatives": pruned}


def sanitize_extension_references(
    agent1: Agent1TaskOutput,
    agent2: Agent2TaskOutput,
) -> Agent2TaskOutput:
    """Drop out-of-bound item references from an Agent 2 draft in place.

    Pure cleanup (never invents or reorders items): removes references that fall
    outside the Agent 1 candidate scope so borderline LLM hallucinations (e.g.
    completing same-series variant IDs) no longer hard-fail the extension
    validator. Shared ``used_item_ids`` / ``evidence_source_ids`` are
    intersected for every task type; ITEM_ADVICE additionally prunes the result
    body, and flexible OUTFIT_MODIFY drafts drop duplicate core-slot /
    non-stackable items the LLM packed into one alternative. Returns the same
    object when nothing needed cleaning.
    """
    wardrobe_scope = set(agent1.candidate_item_ids)
    wardrobe_scope.update(agent1.facts.get("current_item_ids", []))

    used_kept = sorted(set(agent2.used_item_ids) & wardrobe_scope)
    evidence_ids = {str(item.source_id) for item in agent1.evidence}
    evidence_kept = sorted(set(agent2.evidence_source_ids) & evidence_ids)

    result = agent2.result
    if agent1.task_type is TaskType.ITEM_ADVICE:
        anchor_id = str((agent1.facts.get("anchor_item") or {}).get("item_id", ""))
        allowed = set(agent1.candidate_item_ids)
        if anchor_id:
            allowed.add(anchor_id)
        result = _sanitize_item_advice_result(result, allowed, anchor_id)
    elif agent1.task_type is TaskType.OUTFIT_MODIFY and agent1.facts.get(
        "adjustment_mode"
    ) == "flexible":
        result = _prune_flexible_duplicates(agent1, result)

    changed = (
        used_kept != sorted(agent2.used_item_ids)
        or evidence_kept != sorted(agent2.evidence_source_ids)
        or result is not agent2.result
    )
    if not changed:
        return agent2
    return agent2.model_copy(
        update={
            "used_item_ids": used_kept,
            "evidence_source_ids": evidence_kept,
            "result": result,
        }
    )


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
