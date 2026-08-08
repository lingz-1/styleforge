from styleforge.core.schemas import CatalogItem, ImageStatus
from styleforge.core.scoring import item_formality_score


def _item(name: str, item_type: str, color: str) -> CatalogItem:
    return CatalogItem(
        item_id=name,
        source="test",
        gender="women",
        item_type=item_type,
        main_category="test",
        name=name,
        color=color,
        description="",
        features=(),
        image_filename="test.jpg",
        relative_image_path="women/test/test.jpg",
        image_status=ImageStatus.AVAILABLE,
    )


def test_business_formality_penalizes_acid_wash_jeans() -> None:
    jeans = _item("Acid Wash Distressed Skinny Jeans", "pants", "Acid Blue")
    trousers = _item("Tailored Wool Trousers", "pants", "Charcoal Gray")

    assert item_formality_score(trousers, "business") > item_formality_score(
        jeans, "business"
    )


def test_business_formality_rewards_professional_shoes() -> None:
    loafers = _item("Structured Leather Penny Loafers", "shoes", "Black")
    sneakers = _item("Athletic Running Sneakers", "shoes", "Neon Orange")

    assert item_formality_score(loafers, "business") > item_formality_score(
        sneakers, "business"
    )
