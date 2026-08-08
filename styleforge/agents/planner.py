"""Planner agent: convert a natural-language request into a strict task."""

from __future__ import annotations

from styleforge.core.garment_attributes import subtype_prompt
from styleforge.core.request_parser import parse_request
from styleforge.core.schemas import TaskSpec
from styleforge.core.slots import base_slot


AUDIENCE_PROMPT_PREFIXES = {
    "women": "women's",
    "men": "men's",
    "girls": "girls'",
    "boys": "boys'",
    "baby": "baby",
    "life": "lifestyle",
}


def _audience_prefix(task: TaskSpec) -> str:
    return " or ".join(
        AUDIENCE_PROMPT_PREFIXES.get(audience, audience)
        for audience in task.target_audiences
    )


def _audience_item(task: TaskSpec, item_label: str) -> str:
    prefix = _audience_prefix(task)
    return f"{prefix} {item_label}" if prefix else item_label


class PlannerAgent:
    """Deterministic offline planner with a replaceable agent boundary."""

    backend = "deterministic"

    def plan(self, user_id: str, request: str, max_results: int = 3) -> TaskSpec:
        return parse_request(user_id, request, max_results)

    def retrieval_prompt(self, task: TaskSpec, slot: str) -> str:
        constraint_slot = base_slot(slot)
        slot_labels = {
            "top": "top or blouse",
            "bottom": "pants or skirt",
            "footwear": "shoes",
            "one_piece": "dress, jumpsuit, or suit",
            "outerwear": "coat or jacket",
            "bag": "handbag or bag",
            "accessory": "fashion accessory",
            "swimwear": "swimsuit or bikini set",
            "swim_bottom": "bikini or swimwear bottom",
            "swim_coverup": "beachwear cover-up",
            "skiwear": "skiwear outfit",
            "base_layer_top": "ski base-layer top",
            "base_layer_bottom": "ski base-layer bottom",
            "activewear_bra": "sports bra",
            "sleepwear": "sleepwear or pajamas",
            "underwear": "underwear",
            "bathwear": "bathrobe or bathwear",
        }
        occasion_prompts = {
            "business": (
                "for a modern professional job interview outfit, polished, structured, "
                "business appropriate, avoiding casual denim and sporty details"
            ),
            "formal": "for an elegant formal outfit with refined structured details",
            "casual": "for a relaxed modern casual outfit",
            "date": "for a polished modern date outfit",
            "sport": "for a practical athletic outfit",
            "daily": "for a versatile everyday outfit",
        }
        required_types = task.required_item_types_by_slot.get(
            slot,
            task.required_item_types_by_slot.get(constraint_slot, ()),
        )
        required_subtypes = task.required_subtypes_by_slot.get(
            slot,
            task.required_subtypes_by_slot.get(constraint_slot, ()),
        )
        type_labels = {
            "skirt": "skirt",
            "pants": "pants or trousers",
            "dress": "dress",
            "jumpsuit": "jumpsuit",
            "suit": "suit",
        }
        audience_prefix = _audience_prefix(task)
        if required_subtypes:
            item_prompt = " or ".join(
                subtype_prompt(value, audience_prefix) for value in required_subtypes
            )
        elif required_types:
            item_prompt = " or ".join(
                _audience_item(task, type_labels.get(value, value))
                for value in required_types
            )
        else:
            item_prompt = _audience_item(
                task,
                slot_labels.get(constraint_slot, constraint_slot),
            )
        parts = [
            item_prompt,
            occasion_prompts.get(task.occasion, f"for a {task.occasion} outfit"),
        ]
        if task.preferred_colors:
            parts.append("in " + " or ".join(task.preferred_colors))
        if task.excluded_colors:
            parts.append("avoiding " + " or ".join(task.excluded_colors))
        excluded_subtypes = task.excluded_subtypes_by_slot.get(
            slot,
            task.excluded_subtypes_by_slot.get(constraint_slot, ()),
        )
        if excluded_subtypes:
            parts.append(
                "avoiding "
                + " or ".join(
                    subtype_prompt(value, audience_prefix)
                    for value in excluded_subtypes
                )
            )
        return " ".join(parts)
