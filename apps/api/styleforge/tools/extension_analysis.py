"""Agent 1 tools: resolve task intent and retrieve grounded wardrobe facts."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from styleforge.core.candidate_service import FeasibilityState, build_candidate_pool
from styleforge.core.categories import infer_slot
from styleforge.core.garment_attributes import item_matches_subtype
from styleforge.core.relaxation import build_relaxation_plan
from styleforge.core.request_spec import ChangeAction, RequestSpec, interpret_request
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

# How many candidate UUIDs resolve to named item cards in Agent 1 facts. Agent 2
# sees bare UUIDs everywhere else, so the named prefix lets it match a concrete
# request word ("帽子"/戒指/项链) instead of guessing blind.
_CANDIDATE_TEXT_LIMIT = 120


def _candidate_item_texts(
    wardrobe, item_ids: list[str], limit: int | None = None
) -> list[dict[str, str]]:
    """Resolve candidate UUIDs to visible item cards (name + type) for Agent 2.

    The cards carry exactly what bare UUIDs hide: the item type and name the
    composer needs to pick a hat when the user says "帽子" or a necklace when
    the user says "项链". Order follows ``item_ids`` (already prioritised by the
    caller), capped by ``limit`` to bound prompt tokens.
    """
    by_id = {item.item_id: item for item in wardrobe}
    cards: list[dict[str, str]] = []
    for uid in item_ids:
        item = by_id.get(uid)
        if item is None:
            continue
        cards.append(
            {"item_id": uid, "name": item.name or "", "item_type": item.item_type}
        )
        if limit is not None and len(cards) >= limit:
            break
    return cards


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
    # 运动/内衣/家居/泳装等特殊槽位按身体部位归类：上半身类视同上装，
    # 下半身类视同下装，其余按完整搭配处理，保证 anchor 槽位永远有模板可套。
    "base_layer_top": (("bottom", "footwear"),),
    "activewear_bra": (("bottom", "footwear"),),
    "sleepwear": (("bottom", "footwear"),),
    "underwear": (("bottom", "footwear"),),
    "swimwear": (("bottom", "footwear"),),
    "swim_coverup": (("bottom", "footwear"),),
    "skiwear": (("top", "bottom", "footwear"), ("one_piece", "footwear")),
    "bathwear": (("top", "bottom", "footwear"), ("one_piece", "footwear")),
    "base_layer_bottom": (("top", "footwear"),),
    "swim_bottom": (("top", "footwear"),),
    "other": (("top", "bottom", "footwear"), ("one_piece", "footwear")),
}

_REQUESTED_SUPPORT_SLOT_TERMS = {
    "top": ("上衣", "衬衫", "毛衣", "针织衫"),
    "bottom": ("下装", "裤子", "半身裙"),
    "footwear": ("鞋", "靴"),
    "outerwear": ("外套", "外搭", "开衫", "风衣", "大衣", "夹克"),
    "bag": ("包", "手拿包", "托特包"),
    "accessory": ("配饰", "首饰", "耳环", "耳饰", "项链", "手链", "戒指", "帽子"),
}


def _requested_support_slots(request: str, anchor_slot: str) -> list[str]:
    """Extract only support slots the user explicitly names.

    Anchor words are deliberately narrower than the general router vocabulary:
    a request containing ``连衣裙`` must not accidentally request a bottom slot.
    The returned order follows the stable slot map so prompts and reports stay
    reproducible.
    """
    text = request.strip().lower()
    return [
        slot
        for slot, terms in _REQUESTED_SUPPORT_SLOT_TERMS.items()
        if slot != anchor_slot and any(term in text for term in terms)
    ]


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


def _bounded_balanced_candidates(
    wardrobe,
    current_ids: list[str],
    priority_ids: list[str],
    *,
    limit: int = _CANDIDATE_TEXT_LIMIT,
    priority_budget: int = 40,
) -> list[str]:
    """Bound the flexible-rebuild pool while keeping every core slot covered.

    The full wardrobe can be thousands of items; dumping every UUID into the
    Agent 2 prompt blows the context window (two copies of the same bare list
    alone were ~166k chars, far over the DeepSeek 64k limit). ``priority_ids``
    (required-slot + direction-matched items) come first, then the rest of the
    wardrobe is filled slot-balanced up to ``limit`` so the composer can rebuild
    a complete outfit without starving any slot. Current-outfit items are
    excluded here; Agent 2 sees them separately as ``current_item_ids``.
    """
    seen: set[str] = set(current_ids)
    chosen: list[str] = []
    for uid in priority_ids:
        uid = str(uid)
        if uid in seen:
            continue
        chosen.append(uid)
        seen.add(uid)
        if len(chosen) >= priority_budget:
            break
    if len(chosen) >= limit:
        return chosen[:limit]
    # Bucket the remaining wardrobe by slot, preserving wardrobe order.
    by_slot: dict[str, list[str]] = {}
    for item in wardrobe:
        uid = item.item_id
        if uid in seen:
            continue
        by_slot.setdefault(infer_slot(item.item_type), []).append(uid)
    # Round-robin across slots so no slot is starved within the budget.
    slots = list(by_slot)
    while len(chosen) < limit and slots:
        for slot in list(slots):
            bucket = by_slot[slot]
            if not bucket:
                slots.remove(slot)
                continue
            chosen.append(bucket.pop(0))
            if len(chosen) >= limit:
                break
    return chosen[:limit]


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


def _flexible_adjustment(
    outfit_id: str,
    wardrobe,
    current_ids: list[str],
    request: str,
    route: TaskRoute,
    retriever: KnowledgeRetriever | None,
    *,
    required_slot: str,
) -> Agent1TaskOutput:
    """Build the Agent 1 contract for a free-form rebuild of the current outfit.

    Nothing is locked and the replaced set is the LLM's own choice: the composer
    may keep or drop any current item and re-pick freely from the candidate
    pool. When ``required_slot`` is set (a slot the current outfit lacks, e.g.
    "加条项链" with no accessory), candidates for that slot are listed first so
    the rebuilt outfit is expected to gain it; ``required_slot_item_ids`` makes
    the guarantee checkable by the hard validator without a slot map.

    Candidate ordering: required-slot items, then direction-matched wardrobe
    items (the knowledge base explains what "更正式一点" etc. means), then the
    rest of the wardrobe as fallback so the composer is never starved.
    """
    required_ids: list[str] = []
    if required_slot:
        required_ids = [
            item.item_id
            for item in wardrobe
            if infer_slot(item.item_type) == required_slot and item.item_id not in current_ids
        ]
    direction_ids: list[str] = []
    if retriever is not None:
        entries = retriever.search(request, kind="style", limit=6)[1]
        if entries:
            direction_ids = [str(item["item_id"]) for item in _knowledge_matches(wardrobe, entries)]
    # Bound the pool: a full wardrobe of UUIDs blows the Agent 2 prompt context
    # (two serialised copies alone were ~166k chars), so priority items come
    # first and the remainder is a slot-balanced sample capped at the limit.
    replacement_ids = _bounded_balanced_candidates(
        wardrobe, current_ids, required_ids + direction_ids
    )
    if not replacement_ids:
        replacement_ids = [item.item_id for item in wardrobe]
    # Named cards let Agent 2 see what each candidate actually is (hat vs ring),
    # so it can match a concrete request word rather than guess from UUIDs.
    # Cover the whole bounded pool — when direction retrieval misses, the pool
    # is the slot-balanced fallback, and the composer still needs names.
    candidate_texts = _candidate_item_texts(
        wardrobe, replacement_ids, limit=_CANDIDATE_TEXT_LIMIT
    )
    by_id = {item.item_id: item for item in wardrobe}
    # Type map over the same pool + current outfit (bounded token cost); the
    # validator uses it to reject duplicate core slots such as two pairs of
    # shoes. Candidates without a card carry no type and are skipped there.
    typed_ids = list(replacement_ids) + list(current_ids)
    candidate_item_types = {
        uid: by_id[uid].item_type for uid in typed_ids if uid in by_id
    }
    facts: dict[str, Any] = {
        "current_outfit_id": outfit_id,
        "current_item_ids": current_ids,
        "locked_item_ids": [],
        "replaced_item_ids": [],
        "replacement_item_ids": replacement_ids,
        "adjustment_mode": "flexible",
        "candidate_item_texts": candidate_texts,
        "current_item_texts": _candidate_item_texts(wardrobe, current_ids),
        "candidate_item_types": candidate_item_types,
    }
    if required_slot:
        facts["required_slot"] = required_slot
        facts["required_slot_item_ids"] = [str(item_id) for item_id in required_ids]
        intent_summary = f"当前搭配缺少 {required_slot}，自主重建整套以补充该槽位"
    else:
        intent_summary = "整体调整当前搭配（正式度/颜色/风格方向）"
    return Agent1TaskOutput(
        task_type=route.task_type,
        intent_summary=intent_summary,
        resolved_target={"target_slot": "", "current_outfit_id": outfit_id},
        constraints={
            "locked_item_ids": [],
            "replaced_item_ids": [],
            "adjustment_mode": "flexible",
        },
        facts=facts,
        candidate_item_ids=replacement_ids,
    )


def _describe_changes(spec: RequestSpec) -> str:
    """One-line intent summary derived from the parsed change set."""
    verb = {
        ChangeAction.ADD: "补充",
        ChangeAction.REMOVE: "移除",
        ChangeAction.REPLACE: "替换为",
        ChangeAction.ADD_OR_REPLACE: "补充或替换为",
        ChangeAction.KEEP: "保持",
    }
    parts: list[str] = []
    for change in spec.changes:
        target = change.target
        label = (
            change.source_text
            or target.item_type
            or target.subtype
            or target.slot
            or "该单品"
        )
        parts.append(f"{verb.get(change.action, change.action.value)}{label}")
    for lock in spec.locks:
        parts.append(f"锁定{lock.source_text}")
    return "；".join(parts)


def _structured_modify(
    outfit_id: str,
    current_ids: list[str],
    wardrobe,
    pool,
    spec: RequestSpec,
    route: TaskRoute,
) -> Agent1TaskOutput:
    """Agent 1 contract for a change-set-driven modify.

    The user expressed explicit changes (add/remove/replace/lock); CandidateService
    turned those into facts: which current items stay locked/kept, which are
    replaced, and a constraint-aware candidate pool (REMOVE targets excluded
    pool-wide, positive changes retrieved by their own target).  ``target_slot``
    no longer decides what is searched or locked -- the change set does.
    """
    locked_ids = pool.lock_ids + pool.keep_ids
    replacement_ids = pool.candidate_item_ids
    by_id = {item.item_id: item for item in wardrobe}
    candidate_texts = _candidate_item_texts(
        wardrobe, replacement_ids, limit=_CANDIDATE_TEXT_LIMIT
    )
    typed_ids = list(replacement_ids) + list(current_ids)
    candidate_item_types = {
        uid: by_id[uid].item_type for uid in typed_ids if uid in by_id
    }
    relaxation = build_relaxation_plan(pool, spec, wardrobe)
    feasibility_report = {
        "state": pool.feasibility.value,
        "unmet_constraints": pool.unmet_constraints,
        "excluded_item_types": pool.excluded_type_subtypes,
        "excluded_colors": pool.excluded_colors,
        "per_change_candidates": pool.add_change_candidates,
        "coverage": [
            {
                "change_id": cov.change_id,
                "action": cov.action.value,
                "strength": cov.strength.value,
                "source_text": cov.source_text,
                "target": cov.target.model_dump(),
                "exact_ids": cov.exact_ids,
                "relaxed_ids": cov.relaxed_ids,
                "all_target_ids": cov.all_target_ids,
                "prefer_missed_ids": cov.prefer_missed_ids,
                "unmet_prefer_colors": cov.unmet_prefer_colors,
            }
            for cov in pool.coverage
        ],
        # Generic relaxation policy for every MUST positive change: which levels
        # (exact -> drop PREFER -> drop MUST colour -> broaden type) unlock
        # which candidates, and which change is cheapest to satisfy. Facts for
        # Agent 2's decision; nothing is relaxed here.
        "relaxation_plan": relaxation.model_dump(mode="json"),
    }
    # Concern B: an anaphor CandidateService anchored to a concrete current item
    # ("把这件大衣换成西装" hitting the session's coat) is no longer an open
    # question from the caller's perspective.  Dump the *effective* spec --
    # without the resolved anaphor -- so Agent 2 does not see a stale
    # clarification obligation next to a resolved subject.
    effective_spec = spec
    if (
        pool.feasibility is not FeasibilityState.NEEDS_CLARIFICATION
        and "anaphoric_reference" in spec.unresolved_fields
    ):
        effective_spec = spec.model_copy(
            update={
                "unresolved_fields": [
                    field
                    for field in spec.unresolved_fields
                    if field != "anaphoric_reference"
                ]
            }
        )
    return Agent1TaskOutput(
        task_type=route.task_type,
        intent_summary=_describe_changes(spec),
        resolved_target={"target_slot": "", "current_outfit_id": outfit_id},
        constraints={
            "locked_item_ids": locked_ids,
            "replaced_item_ids": pool.replace_ids,
            "feasibility": pool.feasibility.value,
        },
        facts={
            "current_outfit_id": outfit_id,
            "current_item_ids": current_ids,
            "locked_item_ids": locked_ids,
            "replaced_item_ids": pool.replace_ids,
            "replacement_item_ids": replacement_ids,
            "adjustment_mode": "structured",
            # The parsed intent plus its feasibility facts so Agent 2 can see
            # exactly what was asked and what the pool can offer.
            "request_spec": effective_spec.model_dump(mode="json"),
            "feasibility_report": feasibility_report,
            "candidate_item_texts": candidate_texts,
            "current_item_texts": _candidate_item_texts(wardrobe, current_ids),
            "candidate_item_types": candidate_item_types,
        },
        candidate_item_ids=replacement_ids,
        needs_clarification=pool.feasibility is FeasibilityState.NEEDS_CLARIFICATION,
        clarification_question=(
            "你提到要替换的那件单品缺少具体指向，请补充品类，或从衣橱中指定一件。"
        ),
    )


def _analyze_modify(
    database_path: str,
    task_input: TaskExecutionInput,
    route: TaskRoute,
    wardrobe,
    retriever: KnowledgeRetriever | None = None,
) -> Agent1TaskOutput:
    outfit_id, current_ids = _resolve_current_outfit(database_path, task_input)
    if not current_ids:
        return Agent1TaskOutput(
            task_type=route.task_type,
            intent_summary="在保持其他单品不变的前提下修改当前搭配",
            resolved_target={"target_slot": ""},
            constraints={"locked_non_target_items": True},
            facts={"current_outfit_id": outfit_id, "current_item_ids": current_ids},
            needs_clarification=True,
            clarification_question="请补充当前搭配。",
        )
    wardrobe_by_id = {item.item_id: item for item in wardrobe}
    missing_ids = sorted(set(current_ids) - set(wardrobe_by_id))
    if missing_ids:
        raise ValueError(f"Current outfit contains items outside the active wardrobe: {missing_ids[:3]}")

    spec = interpret_request(task_input.request)
    if spec.changes:
        # Explicit change set: retrieval is constraint-aware, and the router's
        # target_slot no longer decides what is searched or locked.
        pool = build_candidate_pool(spec, wardrobe, current_ids)
        return _structured_modify(outfit_id, current_ids, wardrobe, pool, spec, route)

    # No explicit change (e.g. "更正式一点"): the LLM understands the requested
    # direction and freely rebuilds the whole outfit. Nothing is locked; the
    # pool is direction-matched items with wardrobe fallback.
    raw_slot = task_input.target_slot.strip().lower() or str(route.extracted.get("target_slot", ""))
    target_slot = SLOT_ALIASES.get(raw_slot, raw_slot)
    if target_slot:
        # Rare legacy path: a slot was routed/extracted without a parsed change
        # (e.g. a slot the current outfit lacks). Rebuild so it gains that slot.
        return _flexible_adjustment(
            outfit_id, wardrobe, current_ids, task_input.request, route, retriever,
            required_slot=target_slot,
        )
    return _flexible_adjustment(
        outfit_id, wardrobe, current_ids, task_input.request, route, retriever,
        required_slot="",
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
    requested_support_slots: list[str] = []
    if anchor is not None:
        anchor_slot = infer_slot(anchor.item_type)
        templates = OUTFIT_TEMPLATES.get(anchor_slot, ())
        slots = {slot for template in templates for slot in template}
        requested_support_slots = _requested_support_slots(
            task_input.request, anchor_slot
        )
        slots.update(requested_support_slots)
        compatible = group_compatible_items(anchor, wardrobe, slots)
    facts = {
        "knowledge_entries": entries,
        "wardrobe_matches": matches,
        "anchor_item": item_summary(anchor) if anchor is not None else None,
        "anchor_source": anchor_source,
        "anchor_candidates": [item_summary(item) for item in anchors],
        "compatible_items_by_slot": compatible,
        "requested_support_slots": requested_support_slots,
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
        constraints={
            "lock_anchor_item": True,
            "wardrobe_only_for_supporting_items": True,
            "required_support_slots": requested_support_slots,
        },
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


def _structural_gap_elements(request: str) -> list[dict[str, Any]]:
    """Return narrowly scoped, scenario-specific wardrobe requirements."""
    normalized = request.strip().lower()
    commute_markers = ("通勤", "上班", "职场", "office", "commute", "workwear")
    if not any(marker in normalized for marker in commute_markers):
        return []
    return [
        {
            "id": "slot:top",
            "label": "通勤基础上装",
            "item_types": ["top"],
            "subtypes": [],
            "colors": [],
            "priority": "high",
            "suggestion": "补充衬衫、针织衫等可重复组合的通勤基础上装",
        },
        {
            "id": "slot:bottom",
            "label": "通勤基础下装",
            "item_types": ["pants", "skirt", "shorts"],
            "subtypes": [],
            "colors": [],
            "priority": "high",
            "suggestion": "补充西裤、半身裙等可重复组合的通勤基础下装",
        },
        {
            "id": "slot:footwear",
            "label": "通勤鞋履",
            "item_types": ["shoes"],
            "subtypes": [],
            "colors": [],
            "priority": "medium",
            "suggestion": "补充适合长时间穿着的通勤鞋履",
        },
    ]


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
    structural_elements = _structural_gap_elements(task_input.request)
    existing_element_ids = {str(element.get("id") or "") for element in target_elements}
    target_elements.extend(
        element
        for element in structural_elements
        if str(element.get("id") or "") not in existing_element_ids
    )
    if target_elements:
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
    if structural_elements and not entries:
        target_title = "日常通勤结构覆盖"
    return Agent1TaskOutput(
        task_type=route.task_type,
        intent_summary=(
            f"以 {target_title} 为目标建立需求画像并对比现有衣橱"
            if entries
            else "分析整体衣橱的完整搭配和场景覆盖"
        ),
        resolved_target={
            "analysis_mode": "targeted" if entries or structural_elements else "general",
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
        return _analyze_modify(database_path, task_input, route, wardrobe, retriever)
    if route.task_type in {TaskType.STYLE_ADVICE, TaskType.ITEM_ADVICE}:
        return _analyze_style_or_item(task_input, route, wardrobe, retriever)
    if route.task_type is TaskType.WARDROBE_COMPATIBILITY:
        return _analyze_compatibility(task_input, route, wardrobe, retriever)
    if route.task_type is TaskType.WARDROBE_GAP:
        return _analyze_gap(task_input, route, wardrobe, retriever)
    raise ValueError(f"Unsupported extension task: {route.task_type.value}")
