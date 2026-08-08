from styleforge.core.garment_attributes import SUBTYPE_RULES, item_matches_subtype
from styleforge.core.schemas import CatalogItem, ImageStatus


def _item(name: str, item_type: str) -> CatalogItem:
    return CatalogItem(
        item_id=name,
        source="test",
        gender="women",
        item_type=item_type,
        main_category=item_type,
        name=name,
        color="Gray",
        description="",
        features=(),
        image_filename="test.jpg",
        relative_image_path="women/test/test.jpg",
        image_status=ImageStatus.AVAILABLE,
    )


def test_shirt_constraint_rejects_round_neck_top() -> None:
    shirt = _item("Tailored Collared Button-Up Shirt", "top")
    round_neck = _item("Soft Round Neck Top", "top")

    assert item_matches_subtype(shirt, "shirt") is True
    assert item_matches_subtype(round_neck, "shirt") is False


def test_subtype_taxonomy_covers_every_major_component() -> None:
    covered_types = {
        item_type
        for rule in SUBTYPE_RULES.values()
        for item_type in rule.item_types
    }

    assert {
        "top",
        "pants",
        "skirt",
        "shoes",
        "outwear",
        "dress",
        "jumpsuit",
        "bag",
        "eyewear",
        "neckwear",
        "hats",
        "earrings",
        "necklace",
        "bracelet",
        "rings",
        "watches",
        "hairwear",
        "gloves",
    } <= covered_types
