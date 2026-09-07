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
    completed = result.get("status") == "completed"
    requested_slots = facts.get("requested_support_slots") or []
    compatible = facts.get("compatible_items_by_slot") or {}
    required_by_slot = {
        slot: _item_ids(compatible.get(slot, [])) for slot in requested_slots
    }
    for slot, candidate_ids in required_by_slot.items():
        if completed and not candidate_ids:
            issues.append(f"用户点名槽位 {slot} 在当前衣橱没有候选单品")
    for outfit in result.get("sample_outfits", []):
        outfit_ids = set(outfit.get("item_ids", outfit.get("wardrobe_item_ids", [])))
        referenced.update(outfit_ids)
        if expected_anchor and expected_anchor.get("item_id") not in outfit_ids:
            issues.append("单品建议示例搭配没有保留锚点单品")
        if completed:
            for slot, candidate_ids in required_by_slot.items():
                if candidate_ids and not (outfit_ids & candidate_ids):
                    issues.append(f"单品建议示例搭配缺少用户点名槽位 {slot}")
    if result.get("anchor_source") == "candidate" and expected_anchor:
        referenced.discard(str(expected_anchor.get("item_id", "")))
    if not referenced <= set(agent1.candidate_item_ids):
        issues.append("单品建议引用了 Agent 1 候选范围外单品")
    return issues


def _validate_style_advice(
    agent1: Agent1TaskOutput,
    result: dict[str, Any],
) -> list[str]:
    """Require every claimed wardrobe match to resolve to Agent 1 facts."""
    if result.get("status") != "completed":
        return []
    referenced = _item_ids(result.get("wardrobe_matches", []))
    allowed = set(agent1.candidate_item_ids)
    issues: list[str] = []
    if not result.get("wardrobe_matches"):
        issues.append("风格建议没有提供可落地的真实衣橱单品")
    if not referenced <= allowed:
        issues.append("风格建议引用了 Agent 1 候选范围外单品")
    if len(referenced) != len(result.get("wardrobe_matches", [])):
        issues.append("风格建议包含无法解析到衣橱 ID 的单品")
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


def _sanitize_compatibility_result(
    agent1: Agent1TaskOutput,
    result: dict[str, Any],
    allowed_ids: set[str],
) -> dict[str, Any]:
    """Restore authoritative compatibility facts and rebuild derived claims.

    Compatibility and redundancy scores are derived from Agent 1's deterministic
    item groups. The model may explain those facts, but it cannot turn every item
    in the same broad category into a "similar" product or invent counts.
    """
    facts = agent1.facts
    grouped = _filter_slot_refs(
        facts.get("compatible_items_by_slot") or {}, allowed_ids
    )
    similar = _filter_item_refs(
        facts.get("similar_wardrobe_items") or [], allowed_ids
    )
    outfits: list[dict[str, Any]] = []
    for outfit in result.get("sample_outfits") or []:
        kept_ids = [
            str(item_id)
            for item_id in outfit.get("wardrobe_item_ids") or []
            if str(item_id) in allowed_ids
        ]
        if not kept_ids:
            continue
        copy = dict(outfit)
        copy["wardrobe_item_ids"] = kept_ids
        outfits.append(copy)
    if result.get("status") != "completed":
        return {
            **result,
            "candidate_item": dict(facts.get("candidate_item") or {}),
            "candidate_slot": str(facts.get("candidate_slot") or ""),
            "compatible_items_by_slot": grouped,
            "compatible_item_counts": {
                slot: len(items) for slot, items in grouped.items()
            },
            "similar_wardrobe_items": similar,
            "sample_outfits": outfits,
            "complete_outfit_count": len(outfits),
        }

    compatible_count = sum(len(items) for items in grouped.values())
    compatibility_score = min(
        100.0,
        (60.0 if outfits else 45.0)
        + 5.0 * min(len(outfits), 3)
        + 5.0 * min(len(grouped), 3),
    )
    similarity_scores = [
        float(item.get("similarity_score") or 0.0) for item in similar
    ]
    redundancy_score = (
        min(100.0, max(similarity_scores) + 5.0 * (len(similar) - 1))
        if similarity_scores
        else 0.0
    )
    if compatible_count == 0:
        recommendation = "not_recommended"
    elif compatibility_score >= 75.0 and redundancy_score < 75.0:
        recommendation = "recommended"
    else:
        recommendation = "consider"

    candidate = dict(facts.get("candidate_item") or {})
    candidate_name = str(candidate.get("name") or candidate.get("item_type") or "候选单品")
    slot_counts = "、".join(
        f"{slot} {len(items)} 件" for slot, items in sorted(grouped.items())
    ) or "没有可验证的互补单品"
    if similar:
        similar_names = "、".join(
            str(item.get("name") or item.get("item_type") or item.get("item_id"))
            for item in similar[:3]
        )
        redundancy_text = f"找到 {len(similar)} 件有显式颜色或子类型重合的相似单品：{similar_names}"
    else:
        redundancy_text = "未发现有显式颜色或子类型重合的相似单品"
    recommendation_text = (
        f"{candidate_name}在当前衣橱中找到{slot_counts}，"
        f"可组成 {len(outfits)} 套已列出的候选搭配；{redundancy_text}。"
    )
    return {
        **result,
        "candidate_item": candidate,
        "candidate_slot": str(facts.get("candidate_slot") or ""),
        "compatibility_score": compatibility_score,
        "recommendation": recommendation,
        "recommendation_text": recommendation_text,
        "compatible_items_by_slot": grouped,
        "compatible_item_counts": {
            slot: len(items) for slot, items in grouped.items()
        },
        "similar_wardrobe_items": similar,
        "sample_outfits": outfits,
        "complete_outfit_count": len(outfits),
        "redundancy_score": redundancy_score,
        "evidence": [
            {
                "source": "deterministic_compatibility",
                "detail": f"按衣橱元数据统计：{slot_counts}；完整候选 {len(outfits)} 套。",
            },
            {
                "source": "deterministic_redundancy",
                "detail": redundancy_text + "。",
            },
        ],
        "limitations": [
            "兼容性仅依据候选品类、显式颜色或子类型及当前衣橱元数据；未识别属性不会推断。"
        ],
    }


def _sanitize_gap_result(
    agent1: Agent1TaskOutput,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Restore deterministic wardrobe counts and gap identities."""
    facts = agent1.facts
    gaps = list(facts.get("missing_elements") or [])
    if gaps:
        labels = [str(gap.get("label") or gap.get("name") or gap.get("id")) for gap in gaps]
        summary = "当前目标仍有结构性缺口：" + "、".join(value for value in labels if value)
    else:
        summary = "按当前衣橱槽位统计，目标场景所需核心类别已覆盖，未发现结构性缺口。"
    return {
        **result,
        "analysis_mode": str(
            (agent1.resolved_target or {}).get("analysis_mode") or "general"
        ),
        "target": dict(agent1.resolved_target or {}),
        "wardrobe_item_count": int(facts.get("wardrobe_item_count") or 0),
        "slot_counts": dict(facts.get("slot_counts") or {}),
        "covered_elements": list(facts.get("covered_elements") or []),
        "gaps": gaps,
        "gap_count": len(gaps),
        "summary": summary,
    }


def _sanitize_style_result(
    agent1: Agent1TaskOutput,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Bind style advice to real wardrobe cards and known preferences.

    A compliant draft keeps its semantic prose, but its wardrobe rows are
    restored from Agent 1 facts so names/colors cannot drift. If the draft
    cited an unknown item (including a name without an item id), rebuild the
    explanatory prose from deterministic, non-product-specific principles.
    """
    if result.get("status") != "completed":
        return result
    facts = agent1.facts
    allowed = set(agent1.candidate_item_ids)
    authoritative = [
        dict(item)
        for item in (
            list(facts.get("wardrobe_matches") or [])
            + list(facts.get("style_wardrobe_items") or [])
        )
        if str(item.get("item_id") or "") in allowed
    ]
    by_id = {str(item["item_id"]): item for item in authoritative}
    requested_rows = list(result.get("wardrobe_matches") or [])
    requested_ids = [str(item.get("item_id") or "") for item in requested_rows]
    ungrounded = (
        not requested_rows
        or any(not item_id or item_id not in by_id for item_id in requested_ids)
    )
    selected = [
        dict(by_id[item_id])
        for item_id in dict.fromkeys(requested_ids)
        if item_id in by_id
    ]
    if not selected:
        selected = list(by_id.values())[:8]

    if not ungrounded:
        usages = {
            str(item.get("item_id") or ""): str(item.get("usage") or "")
            for item in requested_rows
        }
        return {
            **result,
            "wardrobe_matches": [
                {**item, **({"usage": usages[item["item_id"]]} if usages.get(item["item_id"]) else {})}
                for item in selected
            ],
        }

    preferences = list(facts.get("preference_facts") or [])
    positive = [
        str(item.get("value") or "").strip()
        for item in preferences
        if str(item.get("polarity") or "positive") != "negative"
        and str(item.get("value") or "").strip()
    ]
    negative = [
        str(item.get("value") or "").strip()
        for item in preferences
        if str(item.get("polarity") or "") == "negative"
        and str(item.get("value") or "").strip()
    ]
    target = str((agent1.resolved_target or {}).get("style") or result.get("title") or "目标风格")
    baseline = "、".join(positive[:2]) or "日常穿衣偏好"
    avoid = "、".join(negative[:2])
    names = [str(item.get("name") or item.get("item_type") or "").strip() for item in selected]
    names = [name for name in names if name]
    landing = "、".join(names[:3]) or "已列出的衣橱候选"
    avoid_text = f"，并避免把{avoid}作为主导元素" if avoid else ""
    return {
        **result,
        "title": target,
        "summary": (
            f"以{baseline}为日常基线，为“{target}”只增加一个视觉重点；"
            f"下列建议仅使用当前衣橱中的真实单品{avoid_text}。"
        ),
        "principles": [
            {
                "title": "保留偏好基线",
                "content": f"整体延续{baseline}，不要同时改变轮廓、颜色和配饰三个方向。",
            },
            {
                "title": "单点增加亮点",
                "content": "每套只选择一个醒目焦点，其余部分保持克制，避免主题元素堆叠。",
            },
            {
                "title": "用真实衣橱落地",
                "content": f"优先从{landing}中选择，并以返回的 item_id 作为实际搭配依据。",
            },
        ],
        "wardrobe_matches": selected,
        "limitations": list(dict.fromkeys([
            *list(result.get("limitations") or []),
            "原草稿含无法绑定到衣橱的单品描述，已改为真实衣橱事实。",
        ])),
        "generation_mode": "grounded_style_sanitizer",
    }


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
    if agent1.task_type is TaskType.STYLE_ADVICE:
        result = _sanitize_style_result(agent1, result)
    elif agent1.task_type is TaskType.ITEM_ADVICE:
        anchor_id = str((agent1.facts.get("anchor_item") or {}).get("item_id", ""))
        allowed = set(agent1.candidate_item_ids)
        if anchor_id:
            allowed.add(anchor_id)
        result = _sanitize_item_advice_result(result, allowed, anchor_id)
    elif agent1.task_type is TaskType.WARDROBE_COMPATIBILITY:
        result = _sanitize_compatibility_result(agent1, result, wardrobe_scope)
    elif agent1.task_type is TaskType.WARDROBE_GAP:
        result = _sanitize_gap_result(agent1, result)
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
            "summary": str(result.get("summary") or agent2.summary),
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
    result_status = str(result.get("status") or "")
    if result_status and result_status != agent2.status:
        issues.append("结果正文状态与 Agent 2 状态不一致")
    if agent1.task_type is TaskType.ITEM_ADVICE and not agent1.needs_clarification:
        facts = agent1.facts
        anchor = facts.get("anchor_item") or {}
        compatible = facts.get("compatible_items_by_slot") or {}
        requested_slots = facts.get("requested_support_slots") or []
        requested_available = all(compatible.get(slot) for slot in requested_slots)
        any_compatible = any(compatible.values())
        facts_are_feasible = bool(anchor) and requested_available and any_compatible
        expected_status = "completed" if facts_are_feasible else "infeasible"
        if agent2.status != expected_status:
            issues.append(
                f"单品建议状态与确定性候选事实不一致，应为 {expected_status}"
            )
    if agent1.task_type is TaskType.OUTFIT_MODIFY:
        issues.extend(_validate_modify(agent1, result))
    elif agent1.task_type is TaskType.STYLE_ADVICE:
        issues.extend(_validate_style_advice(agent1, result))
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
