"""Agent 1 tools: resolve task intent and retrieve grounded wardrobe facts."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from styleforge.core.categories import infer_slot
from styleforge.core.garment_attributes import item_matches_subtype
from styleforge.knowledge.retriever import KnowledgeRetriever
from styleforge.models.agent_tasks import Agent1TaskOutput
from styleforge.models.context import ContextPack
from styleforge.models.task import TaskExecutionInput
from styleforge.orchestration.task_router import TaskRoute, TaskType
from styleforge.repositories.database import database_session
from styleforge.repositories.wardrobe_repository import list_items
from styleforge.tools.extension_items import (
    candidate_to_catalog_item,
    extract_color,
    extract_item_concept,
    group_compatible_items,
    infer_candidate_item,
    item_summary,
    matching_subtypes,
    resolve_anchor_items,
)


SLOT_ALIASES = {
    "鞋": "footwear",
    "鞋履": "footwear",
    "footwear": "footwear",
    "shoes": "footwear",
    "外套": "outerwear",
    "outerwear": "outerwear",
    "coat": "outerwear",
    "上衣": "top",
    "top": "top",
    "下装": "bottom",
    "裤子": "bottom",
    "裙子": "bottom",
    "bottom": "bottom",
    "包": "bag",
    "bag": "bag",
    "配饰": "accessory",
    "accessory": "accessory",
}

OUTFIT_TEMPLATES = {
    "top": (("bottom", "footwear"),),
    "bottom": (("top", "footwear"),),
    "one_piece": (("footwear",),),
    "footwear": (("top", "bottom"), ("one_piece",)),
    "outerwear": (("top", "bottom", "footwear"), ("one_piece", "footwear")),
    "bag": (("top", "bottom", "footwear"), ("one_piece", "footwear")),
    "accessory": (("top", "bottom", "footwear"), ("one_piece", "footwear")),
}


def _knowledge_match_score(item, entry: dict[str, Any]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    if item.item_type in set(entry.get("preferred_item_types", [])):
        score += 35.0
        reasons.append("品类符合目标知识")
    if set(entry.get("preferred_subtypes", [])) & set(matching_subtypes(item)):
        score += 40.0
        reasons.append("细分类符合目标知识")
    colors = [str(value).lower() for value in entry.get("preferred_colors", [])]
    if item.color and any(value in item.color.lower() for value in colors):
        score += 25.0
        reasons.append("色彩符合目标知识")
    return score, reasons


def _knowledge_matches(wardrobe, entries: list[dict[str, Any]], limit: int = 12):
    ranked: list[tuple[float, str, dict[str, Any]]] = []
    for item in wardrobe:
        best_score = 0.0
        best_reasons: list[str] = []
        for entry in entries:
            score, reasons = _knowledge_match_score(item, entry)
            if score > best_score:
                best_score = score
                best_reasons = reasons
        if best_score > 0:
            ranked.append(
                (
                    best_score,
                    item.item_id,
                    {
                        **item_summary(item),
                        "knowledge_match_score": best_score,
                        "match_reasons": best_reasons,
                    },
                )
            )
    ranked.sort(key=lambda value: (-value[0], value[1]))
    return [payload for _, _, payload in ranked[:limit]]


def _resolve_current_outfit(
    database_path: str,
    task_input: TaskExecutionInput,
) -> tuple[str, list[str]]:
    if task_input.current_item_ids:
        return task_input.current_outfit_id, list(task_input.current_item_ids)
    with database_session(database_path) as connection:
        parameters: tuple[object, ...]
        outfit_filter = ""
        if task_input.current_outfit_id:
            outfit_filter = "AND co.outfit_id = %s"
            parameters = (task_input.user_id, task_input.current_outfit_id)
        else:
            parameters = (task_input.user_id,)
        row = connection.execute(
            f"""
            SELECT co.outfit_id, co.item_ids_json
            FROM candidate_outfits AS co
            JOIN styling_runs AS sr ON sr.run_id = co.run_id
            WHERE sr.user_id = %s AND sr.status = 'completed' {outfit_filter}
            ORDER BY sr.created_at DESC, co.rank ASC
            LIMIT 1
            """,  # noqa: S608
            parameters,
        ).fetchone()
    if row is None:
        return "", []
    return row["outfit_id"], list(json.loads(row["item_ids_json"]))


def _analyze_modify(
    database_path: str,
    task_input: TaskExecutionInput,
    route: TaskRoute,
    wardrobe,
) -> Agent1TaskOutput:
    outfit_id, current_ids = _resolve_current_outfit(database_path, task_input)
    raw_slot = task_input.target_slot.strip().lower() or str(route.extracted.get("target_slot", ""))
    target_slot = SLOT_ALIASES.get(raw_slot, raw_slot)
    if not current_ids:
        return Agent1TaskOutput(
            task_type=route.task_type,
            intent_summary="在保持其他单品不变的前提下修改当前搭配",
            resolved_target={"target_slot": target_slot},
            constraints={"locked_non_target_items": True},
            facts={"current_outfit_id": outfit_id, "current_item_ids": current_ids},
            needs_clarification=True,
            clarification_question="请补充当前搭配。",
        )
    if not target_slot:
        # Overall adjustment (e.g. "更正式一点"): rebuild a complete outfit in
        # the requested direction.  Nothing is locked and every replacement
        # candidate comes from the wardrobe outside the current outfit, so the
        # existing locked/replaced validation passes unchanged.
        replacement_ids = [
            item.item_id
            for item in wardrobe
            if item.item_id not in current_ids
        ][:40]
        return Agent1TaskOutput(
            task_type=route.task_type,
            intent_summary="整体调整当前搭配（正式度/颜色/风格方向）",
            resolved_target={"target_slot": "", "current_outfit_id": outfit_id},
            constraints={
                "locked_item_ids": [],
                "replaced_item_ids": [],
                "adjustment_mode": "overall",
            },
            facts={
                "current_outfit_id": outfit_id,
                "current_item_ids": current_ids,
                "locked_item_ids": [],
                "replaced_item_ids": [],
                "replacement_item_ids": replacement_ids,
                "adjustment_mode": "overall",
            },
            candidate_item_ids=replacement_ids,
        )
    wardrobe_by_id = {item.item_id: item for item in wardrobe}
    missing_ids = sorted(set(current_ids) - set(wardrobe_by_id))
    if missing_ids:
        raise ValueError(f"Current outfit contains items outside the active wardrobe: {missing_ids[:3]}")
    replaced_ids = [
        item_id
        for item_id in current_ids
        if infer_slot(wardrobe_by_id[item_id].item_type) == target_slot
    ]
    if not replaced_ids:
        return Agent1TaskOutput(
            task_type=route.task_type,
            intent_summary=f"只修改当前搭配中的 {target_slot}",
            resolved_target={"target_slot": target_slot},
            constraints={"locked_non_target_items": True},
            facts={"current_outfit_id": outfit_id, "current_item_ids": current_ids},
            needs_clarification=True,
            clarification_question=f"当前搭配中没有 {target_slot} 单品，请重新指定修改位置。",
        )
    locked_ids = [item_id for item_id in current_ids if item_id not in replaced_ids]
    replacement_ids = [
        item.item_id
        for item in wardrobe
        if infer_slot(item.item_type) == target_slot and item.item_id not in current_ids
    ][:40]
    return Agent1TaskOutput(
        task_type=route.task_type,
        intent_summary=f"只替换当前搭配的 {target_slot}，其余单品硬锁定",
        resolved_target={"target_slot": target_slot, "current_outfit_id": outfit_id},
        constraints={
            "locked_item_ids": locked_ids,
            "replaced_item_ids": replaced_ids,
            "only_edit_target_slot": True,
        },
        facts={
            "current_outfit_id": outfit_id,
            "current_item_ids": current_ids,
            "locked_item_ids": locked_ids,
            "replaced_item_ids": replaced_ids,
            "replacement_item_ids": replacement_ids,
        },
        candidate_item_ids=replacement_ids,
    )


def _analyze_style_or_item(
    task_input: TaskExecutionInput,
    route: TaskRoute,
    wardrobe,
    retriever: KnowledgeRetriever,
) -> Agent1TaskOutput:
    kind = "style" if route.task_type is TaskType.STYLE_ADVICE else "item"
    evidence, entries = retriever.search(task_input.request, kind=kind, limit=6)
    matches = _knowledge_matches(wardrobe, entries)
    if route.task_type is TaskType.STYLE_ADVICE:
        target = str(entries[0]["title"]) if entries else task_input.request.strip()
        return Agent1TaskOutput(
            task_type=route.task_type,
            intent_summary=f"理解目标风格 {target}，检索知识依据和衣橱可落地单品",
            resolved_target={"style": target, "knowledge_entry_ids": [e["id"] for e in entries]},
            constraints={"wardrobe_only": True, "knowledge_is_supporting_evidence": True},
            facts={"knowledge_entries": entries, "wardrobe_matches": matches},
            evidence=evidence,
            candidate_item_ids=[str(item["item_id"]) for item in matches],
        )

    anchors = resolve_anchor_items(wardrobe, task_input.request, item_id=task_input.item_id)
    anchor = anchors[0] if len(anchors) == 1 else None
    anchor_source = "wardrobe"
    clarification = ""
    if len(anchors) > 1:
        clarification = "衣橱中找到多件符合描述的单品，请选择具体一件。"
    if anchor is None and not anchors:
        candidate = task_input.candidate_item or infer_candidate_item(task_input.request)
        if candidate is not None:
            anchor = candidate_to_catalog_item(candidate)
            anchor_source = "candidate"
        else:
            clarification = "请说明具体单品的品类、颜色，或从衣橱中选择一件单品。"
            anchor_source = "unresolved"
    compatible: dict[str, list[dict[str, Any]]] = {}
    if anchor is not None:
        anchor_slot = infer_slot(anchor.item_type)
        templates = OUTFIT_TEMPLATES.get(anchor_slot, ())
        slots = {slot for template in templates for slot in template}
        compatible = group_compatible_items(anchor, wardrobe, slots)
    facts = {
        "knowledge_entries": entries,
        "wardrobe_matches": matches,
        "anchor_item": item_summary(anchor) if anchor is not None else None,
        "anchor_source": anchor_source,
        "anchor_candidates": [item_summary(item) for item in anchors],
        "compatible_items_by_slot": compatible,
    }
    candidate_ids = [
        str(item["item_id"])
        for slot_items in compatible.values()
        for item in slot_items
    ]
    candidate_ids.extend(str(item["item_id"]) for item in matches)
    if anchor_source == "wardrobe" and anchor is not None:
        candidate_ids.insert(0, anchor.item_id)
    return Agent1TaskOutput(
        task_type=route.task_type,
        intent_summary="定位目标单品，将其作为锚点检索衣橱内可搭配单品",
        resolved_target={
            "item_concept": extract_item_concept(task_input.request) or {},
            "color": extract_color(task_input.request),
            "anchor_source": anchor_source,
        },
        constraints={"lock_anchor_item": True, "wardrobe_only_for_supporting_items": True},
        facts=facts,
        evidence=evidence,
        candidate_item_ids=list(dict.fromkeys(candidate_ids)),
        needs_clarification=bool(clarification),
        clarification_question=clarification,
    )


def _analyze_compatibility(
    task_input: TaskExecutionInput,
    route: TaskRoute,
    wardrobe,
    retriever: KnowledgeRetriever,
) -> Agent1TaskOutput:
    candidate_input = task_input.candidate_item or infer_candidate_item(task_input.request)
    if candidate_input is None:
        return Agent1TaskOutput(
            task_type=route.task_type,
            intent_summary="判断候选新品与当前衣橱的兼容性",
            constraints={"candidate_is_transient": True, "wardrobe_only_for_supporting_items": True},
            needs_clarification=True,
            clarification_question="请补充候选新品的品类、颜色或简短描述。",
        )
    candidate = candidate_to_catalog_item(candidate_input)
    anchor_slot = infer_slot(candidate.item_type)
    templates = OUTFIT_TEMPLATES.get(anchor_slot, ())
    if not templates:
        return Agent1TaskOutput(
            task_type=route.task_type,
            intent_summary="判断候选新品与当前衣橱的兼容性",
            resolved_target={"candidate_item": candidate_input.model_dump(mode="json")},
            constraints={"candidate_is_transient": True},
            needs_clarification=True,
            clarification_question="暂时无法识别该新品的搭配槽位，请补充准确品类。",
        )
    slots = {slot for template in templates for slot in template}
    compatible = group_compatible_items(candidate, wardrobe, slots)
    query = " ".join(
        value
        for value in (candidate.name, candidate_input.subtype, candidate.description)
        if value
    )
    evidence, entries = retriever.search(query, kind="item", limit=4)
    candidate_subtypes = set(matching_subtypes(candidate))
    similar_ranked: list[tuple[float, str, dict[str, Any]]] = []
    for item in wardrobe:
        if item.item_type != candidate.item_type:
            continue
        score = 40.0
        if candidate_subtypes & set(matching_subtypes(item)):
            score += 35.0
        if candidate.color and candidate.color.lower() in item.color.lower():
            score += 25.0
        similar_ranked.append((score, item.item_id, {**item_summary(item), "similarity_score": score}))
    similar_ranked.sort(key=lambda value: (-value[0], value[1]))
    similar_items = [payload for _, _, payload in similar_ranked[:8]]
    candidate_ids = [
        str(item["item_id"])
        for slot_items in compatible.values()
        for item in slot_items
    ]
    candidate_ids.extend(str(item["item_id"]) for item in similar_items)
    return Agent1TaskOutput(
        task_type=route.task_type,
        intent_summary="解析候选新品属性，检索衣橱中的互补单品和相似单品",
        resolved_target={
            "candidate_item": candidate_input.model_dump(mode="json"),
            "candidate_slot": anchor_slot,
        },
        constraints={"candidate_is_transient": True, "never_insert_candidate": True},
        facts={
            "candidate_item": candidate_input.model_dump(mode="json"),
            "candidate_slot": anchor_slot,
            "compatible_items_by_slot": compatible,
            "similar_wardrobe_items": similar_items,
            "knowledge_entries": entries,
        },
        evidence=evidence,
        candidate_item_ids=list(dict.fromkeys(candidate_ids)),
    )


def _safe_subtype_match(item, subtype: str) -> bool:
    try:
        return item_matches_subtype(item, subtype)
    except ValueError:
        return False


def _target_elements(entry: dict[str, Any]) -> list[dict[str, Any]]:
    explicit = entry.get("target_elements", [])
    if explicit:
        return [dict(element) for element in explicit]
    elements: list[dict[str, Any]] = []
    for item_type in entry.get("preferred_item_types", []):
        elements.append(
            {
                "id": f"type:{item_type}",
                "label": str(item_type),
                "item_types": [item_type],
                "subtypes": [],
                "colors": [],
                "priority": "medium",
                "suggestion": f"补充能体现目标风格的 {item_type} 单品",
            }
        )
    return elements


def _analyze_gap(
    task_input: TaskExecutionInput,
    route: TaskRoute,
    wardrobe,
    retriever: KnowledgeRetriever,
) -> Agent1TaskOutput:
    evidence, entries = retriever.search(task_input.request, kind="style", limit=6)
    slot_counts = Counter(infer_slot(item.item_type) for item in wardrobe)
    type_counts = Counter(item.item_type for item in wardrobe)
    color_counts = Counter(item.color.strip().lower() for item in wardrobe if item.color.strip())
    target_elements: list[dict[str, Any]] = []
    covered_elements: list[dict[str, Any]] = []
    missing_elements: list[dict[str, Any]] = []
    if entries:
        target_elements = _target_elements(entries[0])
        for element in target_elements:
            item_types = set(element.get("item_types", []))
            subtypes = set(element.get("subtypes", []))
            colors = {str(value).lower() for value in element.get("colors", [])}
            matches = []
            for item in wardrobe:
                type_match = not item_types or item.item_type in item_types
                subtype_match = not subtypes or any(
                    _safe_subtype_match(item, subtype) for subtype in subtypes
                )
                color_match = not colors or any(
                    color in item.color.lower() for color in colors
                )
                if type_match and subtype_match and color_match:
                    matches.append(item.item_id)
            payload = {**element, "matching_item_ids": matches[:8]}
            if matches:
                covered_elements.append(payload)
            else:
                missing_elements.append(payload)
    target_title = str(entries[0]["title"]) if entries else "整体衣橱覆盖"
    return Agent1TaskOutput(
        task_type=route.task_type,
        intent_summary=(
            f"以 {target_title} 为目标建立需求画像并对比现有衣橱"
            if entries
            else "分析整体衣橱的完整搭配和场景覆盖"
        ),
        resolved_target={
            "analysis_mode": "targeted" if entries else "general",
            "style": target_title if entries else "",
            "knowledge_entry_ids": [entry["id"] for entry in entries],
        },
        constraints={"no_brand_or_product_hallucination": True, "wardrobe_only": True},
        facts={
            "wardrobe_item_count": len(wardrobe),
            "slot_counts": dict(sorted(slot_counts.items())),
            "item_type_counts": dict(sorted(type_counts.items())),
            "top_colors": color_counts.most_common(8),
            "target_elements": target_elements,
            "covered_elements": covered_elements,
            "missing_elements": missing_elements,
            "knowledge_entries": entries,
        },
        evidence=evidence,
        candidate_item_ids=[
            item_id
            for element in covered_elements
            for item_id in element.get("matching_item_ids", [])
        ],
    )


def analyze_extension_task(
    *,
    database_path: str,
    knowledge_root: Path,
    task_input: TaskExecutionInput,
    route: TaskRoute,
    context_pack: ContextPack,
    knowledge_retriever: KnowledgeRetriever | None = None,
) -> Agent1TaskOutput:
    """Retrieve facts for one extension task without producing its final answer."""
    del context_pack  # The typed snapshot is supplied to the Agent/LLM prompt separately.
    with database_session(database_path) as connection:
        wardrobe = list_items(connection, task_input.user_id)
    retriever = (
        knowledge_retriever
        if knowledge_retriever is not None
        else KnowledgeRetriever(knowledge_root)
    )
    if route.task_type is TaskType.OUTFIT_MODIFY:
        return _analyze_modify(database_path, task_input, route, wardrobe)
    if route.task_type in {TaskType.STYLE_ADVICE, TaskType.ITEM_ADVICE}:
        return _analyze_style_or_item(task_input, route, wardrobe, retriever)
    if route.task_type is TaskType.WARDROBE_COMPATIBILITY:
        return _analyze_compatibility(task_input, route, wardrobe, retriever)
    if route.task_type is TaskType.WARDROBE_GAP:
        return _analyze_gap(task_input, route, wardrobe, retriever)
    raise ValueError(f"Unsupported extension task: {route.task_type.value}")
