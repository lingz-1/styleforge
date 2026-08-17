"""Structure ontology: mapping, conflict detection, placement legality.

Covers the deterministic physical side of the environment boundary — never
taste, never intent. Unknown types must stay UNKNOWN (no defaulting to top).
"""

from __future__ import annotations

from styleforge.models.agentic_contract import BodyRegion, GarmentLayer, Placement
from styleforge.agentic.structure import (
    PlacedItem,
    check_structure,
    effective_layer,
    placement_error,
    structure_for,
)


def _placed(item_id: str, item_type: str, layer=None) -> PlacedItem:
    return PlacedItem(
        item_id=item_id,
        structure=structure_for(item_type),
        assigned_layer=layer,
    )


def test_known_types_map_to_structures() -> None:
    assert structure_for("top").allowed_region is BodyRegion.upper_body
    assert structure_for("outwear").allowed_layers == [GarmentLayer.outer]
    assert structure_for("dress").resolves_occupancy() == [
        BodyRegion.upper_body,
        BodyRegion.lower_body,
    ]
    assert structure_for("shoes").allowed_region is BodyRegion.feet
    assert structure_for("necklace").exclusive is False


def test_unknown_type_stays_unknown() -> None:
    assert structure_for("cape") is None
    assert structure_for("poncho") is None
    assert structure_for("unknown_imported_category") is None


def test_layered_tops_are_legal() -> None:
    # t-shirt (base) + cardigan/coat (outer) coexist.
    check = check_structure(
        [
            _placed("tshirt", "top", GarmentLayer.base),
            _placed("coat", "outwear", GarmentLayer.outer),
        ]
    )
    assert check.valid is True
    assert check.issues == []


def test_two_exclusive_bottoms_conflict() -> None:
    check = check_structure(
        [
            _placed("pants_a", "pants", GarmentLayer.base),
            _placed("skirt_b", "skirt", GarmentLayer.base),
        ]
    )
    assert check.valid is False
    assert any("lower_body/base" in issue for issue in check.issues)


def test_dress_plus_coat_is_legal() -> None:
    # Dress occupies (upper,base)+(lower,base); coat occupies (upper,outer).
    # Conflict is judged at (region, layer), never region alone.
    check = check_structure(
        [
            _placed("dress", "dress", GarmentLayer.base),
            _placed("coat", "outwear", GarmentLayer.outer),
        ]
    )
    assert check.valid is True


def test_dress_plus_skirt_conflicts() -> None:
    check = check_structure(
        [
            _placed("dress", "dress", GarmentLayer.base),
            _placed("skirt", "skirt", GarmentLayer.base),
        ]
    )
    assert check.valid is False


def test_two_pairs_of_shoes_conflict() -> None:
    check = check_structure(
        [
            _placed("boots", "shoes", GarmentLayer.base),
            _placed("sneakers", "shoes", GarmentLayer.base),
        ]
    )
    assert check.valid is False
    assert any("feet/base" in issue for issue in check.issues)


def test_accessories_coexist() -> None:
    check = check_structure(
        [
            _placed("necklace", "necklace", GarmentLayer.base),
            _placed("earrings", "earrings", GarmentLayer.base),
            _placed("bag", "bag", GarmentLayer.base),
        ]
    )
    assert check.valid is True


def test_unknown_items_are_surfaced_not_conflicts() -> None:
    check = check_structure([_placed("cape_x", "cape"), _placed("tshirt", "top")])
    assert check.valid is True
    assert check.unknown_item_ids == ["cape_x"]


def test_placement_legality() -> None:
    top = structure_for("top")
    shoes = structure_for("shoes")
    assert placement_error(top, Placement(region=BodyRegion.upper_body, layer=GarmentLayer.mid)) is None
    assert placement_error(top, Placement(region=BodyRegion.upper_body, layer=GarmentLayer.outer)) is not None
    assert placement_error(shoes, Placement(region=BodyRegion.upper_body, layer=GarmentLayer.base)) is not None
    assert placement_error(None, Placement(region=BodyRegion.upper_body, layer=GarmentLayer.base)) is not None


def test_effective_layer_fallback() -> None:
    assert effective_layer(structure_for("top")) is GarmentLayer.base
    assert effective_layer(structure_for("outwear")) is GarmentLayer.outer
    assert effective_layer(structure_for("shoes")) is GarmentLayer.base
