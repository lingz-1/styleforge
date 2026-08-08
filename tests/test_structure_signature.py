from tests.helpers import make_item

from styleforge.core.structure_signature import (
    color_family,
    structure_signature,
    style_tags,
)


def test_color_family_returns_dark_neutral_for_charcoal() -> None:
    assert color_family("Charcoal Gray") == "dark_neutral"


def test_color_family_burgundy() -> None:
    assert color_family("Burgundy") == "burgundy"


def test_color_family_blue_light_blue() -> None:
    assert color_family("Light Blue") == "blue"


def test_color_family_unknown_returns_other() -> None:
    assert color_family("Zorpal") == "other"


def test_color_family_empty_returns_other() -> None:
    assert color_family("") == "other"


def test_style_tags_detects_formal() -> None:
    item = make_item("i1", "top", name="Tailored blazer", color="Black")
    assert "formal" in style_tags(item)


def test_style_tags_defaults_to_basic() -> None:
    item = make_item("i1", "top", name="Basic cotton top", color="Black")
    assert style_tags(item) == ("basic",)


def test_structure_signature_counts_layers_and_categories() -> None:
    items = [
        make_item("coat", "outwear", name="Wool coat", color="Charcoal Gray"),
        make_item("top", "top", name="Black blouse", color="Black"),
        make_item("bottom", "pants", name="Navy trousers", color="Navy"),
        make_item("shoes", "shoes", name="Black loafers", color="Black"),
    ]
    signature = structure_signature(items)

    assert signature.category_structure == ("bottom", "footwear", "outerwear", "top")
    assert signature.layer_count == 2  # coat + top
    assert "dark_neutral" in signature.dominant_color_family
    assert signature.style_mix


def test_structure_signature_empty() -> None:
    signature = structure_signature([])
    assert signature.category_structure == ()
    assert signature.layer_count == 0


def test_structure_signature_to_dict() -> None:
    signature = structure_signature([make_item("top", "top", name="Tee", color="Black")])
    payload = signature.to_dict()
    assert payload["category_structure"] == ["top"]
    assert isinstance(payload["layer_count"], int)
