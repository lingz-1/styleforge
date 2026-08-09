"""Rule-based validation of composer proposals (no LLM cost).

Checks item existence, basic category completeness, and duplicate items.
Completeness accepts either separates (top + bottom + footwear) or a one-piece
plus footwear, so both the parsed task and the LLM's own slot choices survive.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

from styleforge.core.categories import infer_slot
from styleforge.core.schemas import CatalogItem, OutfitCandidate
from styleforge.core.slots import base_slot


def _slots_of(item_ids: Sequence[str], items_by_id: dict[str, CatalogItem]) -> set[str]:
    return {
        base_slot(infer_slot(items_by_id[item_id].item_type))
        for item_id in item_ids
        if item_id in items_by_id
    }


def _category_complete(slots: set[str]) -> bool:
    has_one_piece = "one_piece" in slots
    has_top = "top" in slots
    has_bottom = "bottom" in slots
    if has_one_piece:
        # A one-piece conflicts with separate top/bottom pieces.
        if has_top or has_bottom:
            return False
        return "footwear" in slots
    return has_top and has_bottom and "footwear" in slots


def validate_proposals(
    proposals: Sequence[dict[str, Any]],
    available_items: Sequence[CatalogItem],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return proposals that pass item-existence, completeness, and no-dup checks."""
    items_by_id = {item.item_id: item for item in available_items}
    available_ids = set(items_by_id)
    validated: list[dict[str, Any]] = []
    notes: list[str] = []
    for proposal in proposals:
        item_ids = list(proposal.get("item_ids", []))
        outfit_id = proposal.get("outfit_id", "?")
        problems: list[str] = []
        if not item_ids:
            problems.append("没有单品")
        if len(set(item_ids)) != len(item_ids):
            problems.append("存在重复单品")
        if not set(item_ids) <= available_ids:
            problems.append("含候选池外单品")
        if not problems and not _category_complete(_slots_of(item_ids, items_by_id)):
            problems.append("品类不完整（需上装+下装+鞋，或连体装+鞋）")
        if problems:
            notes.append(f"{outfit_id} 未通过基础校验：{'；'.join(problems)}")
        else:
            validated.append(proposal)
    return validated, notes


def proposal_to_candidate(
    proposal: dict[str, Any],
    items_by_id: dict[str, CatalogItem],
    score: float,
    *,
    llm_score: float | None = None,
    rule_score: float | None = None,
) -> OutfitCandidate:
    """Convert a validated proposal dict into an ``OutfitCandidate``."""
    item_ids = tuple(proposal.get("item_ids", []))
    slot_items: dict[str, str] = {}
    for item_id in item_ids:
        item = items_by_id.get(item_id)
        if item is not None:
            slot_items[base_slot(infer_slot(item.item_type))] = item_id
    outfit_id = proposal.get("outfit_id", "")
    if not outfit_id:
        signature = "|".join(item_ids)
        outfit_id = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
    reasons = tuple(
        text
        for text in (proposal.get("reasoning", ""), proposal.get("style_tag", ""))
        if text
    )
    score_details = {}
    if llm_score is not None:
        score_details["llm_score"] = llm_score
    if rule_score is not None:
        score_details["rule_score"] = rule_score
    return OutfitCandidate(
        outfit_id=outfit_id,
        item_ids=item_ids,
        slot_items=slot_items,
        hard_valid=True,
        score=score,
        llm_score=llm_score,
        rule_score=rule_score,
        score_details=score_details,
        reasons=reasons,
    )
