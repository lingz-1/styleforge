from styleforge.pipelines.seed_balanced_wardrobe import (
    MIXED_LARGE_QUOTAS,
    STYLE_ORDER,
    _select_mixed_items,
)
from styleforge.core.schemas import CatalogItem, ImageStatus


def _item(index: int, keyword: str) -> CatalogItem:
    return CatalogItem(
        item_id=f"item-{index}",
        source="test",
        gender="women",
        item_type="top",
        main_category="top",
        name=f"{keyword} top",
        color=f"color-{index % 4}",
        description="",
        features=(),
        image_filename=f"item-{index}.jpg",
        relative_image_path=f"women/top/item-{index}.jpg",
        image_status=ImageStatus.AVAILABLE,
    )


def test_mixed_large_profile_has_broad_core_coverage() -> None:
    assert sum(MIXED_LARGE_QUOTAS.values()) >= 200
    assert MIXED_LARGE_QUOTAS["top"] >= 30
    assert MIXED_LARGE_QUOTAS["shoes"] >= 30


def test_mixed_selection_assigns_each_style() -> None:
    candidates = [
        _item(index, keyword)
        for index, keyword in enumerate(STYLE_ORDER * 2)
    ]

    selected = _select_mixed_items(candidates, len(STYLE_ORDER))

    assert {style for _, style in selected} == set(STYLE_ORDER)
