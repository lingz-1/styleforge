"""Deterministic structure ontology for the Agentic Environment.

This is the *physical* side of the environment boundary: it maps a catalog
item type to the garment's physical structure (region × layer × occupancy ×
exclusivity) and detects whether a set of garments can coexist. It never
judges whether an outfit "looks good" or honours the user's intent — those
belong to the Agent and ``review_outfit``.

Unknown item types deliberately resolve to ``None`` (UNKNOWN). We do NOT fall
back to "top" and we do NOT add one special rule per new test case — an
unknown physical structure is a fact the Agent and the evaluator must handle,
not a case patch to map away.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from styleforge.models.agentic_contract import (
    BodyRegion,
    GarmentLayer,
    GarmentStructure,
    Placement,
)


def _garment(
    region: BodyRegion,
    layers: list[GarmentLayer],
    occupancy: list[BodyRegion] | None = None,
    exclusive: bool = True,
) -> GarmentStructure:
    return GarmentStructure(
        allowed_region=region,
        allowed_layers=layers,
        occupancy=occupancy or [],
        exclusive=exclusive,
    )


UPPER = BodyRegion.upper_body
LOWER = BodyRegion.lower_body
FULL = BodyRegion.full_body
FEET = BodyRegion.feet
ACCESSORY = BodyRegion.accessory
BASE = GarmentLayer.base
MID = GarmentLayer.mid
OUTER = GarmentLayer.outer


# Physical structure per catalog item type, extended from the existing
# TYPE_TO_SLOT vocabulary into the region/layer/occupancy ontology. Types whose
# physical role is genuinely ambiguous (swimwear, skiwear, underwear, "other",
# unknown imported categories) are intentionally absent, so ``structure_for``
# returns None (UNKNOWN) for them.
TYPE_TO_STRUCTURE: dict[str, GarmentStructure] = {
    # tops may sit on base or mid — the Agent picks the layer via placement.
    "top": _garment(UPPER, [BASE, MID]),
    "outwear": _garment(UPPER, [OUTER]),
    "pants": _garment(LOWER, [BASE]),
    "shorts": _garment(LOWER, [BASE]),
    "skirt": _garment(LOWER, [BASE]),
    "dress": _garment(FULL, [BASE], [UPPER, LOWER]),
    "jumpsuit": _garment(FULL, [BASE], [UPPER, LOWER]),
    "suit": _garment(FULL, [BASE], [UPPER, LOWER]),
    "outfit_set": _garment(FULL, [BASE], [UPPER, LOWER]),
    "shoes": _garment(FEET, [], [FEET]),
    "legwear": _garment(LOWER, [BASE]),
    # Accessories are structural cardinality, not uniqueness: several may
    # coexist on (accessory, *) — exclusive=False.
    "bag": _garment(ACCESSORY, [], exclusive=False),
    "belts": _garment(ACCESSORY, [], exclusive=False),
    "jewellery": _garment(ACCESSORY, [], exclusive=False),
    "eyewear": _garment(ACCESSORY, [], exclusive=False),
    "earrings": _garment(ACCESSORY, [], exclusive=False),
    "bracelet": _garment(ACCESSORY, [], exclusive=False),
    "necklace": _garment(ACCESSORY, [], exclusive=False),
    "rings": _garment(ACCESSORY, [], exclusive=False),
    "hats": _garment(ACCESSORY, [], exclusive=False),
    "watches": _garment(ACCESSORY, [], exclusive=False),
    "neckwear": _garment(ACCESSORY, [], exclusive=False),
    "brooch": _garment(ACCESSORY, [], exclusive=False),
    "hairwear": _garment(ACCESSORY, [], exclusive=False),
    "gloves": _garment(ACCESSORY, [], exclusive=False),
}


def structure_for(item_type: str) -> GarmentStructure | None:
    """Physical structure of a catalog item type, or None = UNKNOWN.

    Unknown is deliberate: it is a fact the Agent/evaluator must handle, never
    a default to guess or a case patch to add.
    """
    return TYPE_TO_STRUCTURE.get(item_type.strip().lower())


def effective_layer(structure: GarmentStructure) -> GarmentLayer:
    """The layer a garment occupies when no explicit placement was given.

    Garments with several allowed layers (a top may be base or mid) are read at
    their *lowest* layer, the most conservative choice for conflict detection.
    Feet and accessories have no layers and occupy the base cell.
    """
    if structure.allowed_layers:
        return structure.allowed_layers[0]
    return GarmentLayer.base


@dataclass(frozen=True)
class PlacedItem:
    """One garment in a candidate state, with its declared layer (if any)."""

    item_id: str
    structure: GarmentStructure | None  # None = UNKNOWN
    assigned_layer: GarmentLayer | None = None


@dataclass(frozen=True)
class StructureCheck:
    valid: bool
    issues: list[str]
    unknown_item_ids: list[str]


def check_structure(placed: list[PlacedItem]) -> StructureCheck:
    """Detect (region, layer) conflicts and unknown structures in a state.

    A cell ``(occupied_region, layer)`` conflicts only when more than one
    *exclusive* garment claims it; non-exclusive accessories coexist freely.
    Unknown items are surfaced (the Agent/evaluator must handle them) but are
    not themselves a conflict — their physical cells are simply unknown.
    """
    exclusives: dict[tuple[str, str], list[str]] = defaultdict(list)
    unknown: list[str] = []
    for item in placed:
        if item.structure is None:
            unknown.append(item.item_id)
            continue
        layer = item.assigned_layer or effective_layer(item.structure)
        for region in item.structure.resolves_occupancy():
            cell = (region.value, layer.value)
            if item.structure.exclusive:
                exclusives[cell].append(item.item_id)
    issues: list[str] = []
    for (region, layer), ids in sorted(exclusives.items()):
        if len(ids) > 1:
            issues.append(f"{region}/{layer} 冲突：{'、'.join(sorted(ids))}")
    return StructureCheck(
        valid=not issues,
        issues=issues,
        unknown_item_ids=sorted(unknown),
    )


def placement_error(structure: GarmentStructure | None, placement: Placement) -> str | None:
    """Why this placement is illegal for the garment, or None when legal.

    Layerless garments (feet, accessories) have no ``allowed_layers`` but still
    occupy the base cell — the same rule ``effective_layer`` uses for conflict
    detection, so placement and conflict resolution agree.
    """
    if structure is None:
        return "未知物理结构，无法确定摆放位置"
    if placement.region is not structure.allowed_region:
        return (
            f"不允许在 {placement.region.value}（该单品为 "
            f"{structure.allowed_region.value}）"
        )
    allowed = structure.allowed_layers or [GarmentLayer.base]
    if placement.layer not in allowed:
        layers = [layer.value for layer in allowed]
        return f"不允许在 {placement.layer.value} 层（允许 {layers}）"
    return None
