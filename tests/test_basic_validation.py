from tests.helpers import make_item

from styleforge.tools.basic_validation import validate_proposals

AVAILABLE = [
    make_item("top1", "top", name="Top"),
    make_item("bottom1", "pants", name="Pants"),
    make_item("shoe1", "shoes", name="Shoes"),
    make_item("dress1", "dress", name="Dress"),
    make_item("coat1", "outwear", name="Coat"),
]


def _proposal(outfit_id: str, item_ids: list[str]) -> dict:
    return {"outfit_id": outfit_id, "item_ids": item_ids, "style_tag": "", "reasoning": ""}


def test_accepts_separates_with_footwear() -> None:
    validated, notes = validate_proposals(
        [_proposal("o1", ["top1", "bottom1", "shoe1"])], AVAILABLE
    )
    assert [p["outfit_id"] for p in validated] == ["o1"]
    assert notes == []


def test_accepts_one_piece_with_footwear() -> None:
    validated, _ = validate_proposals([_proposal("o1", ["dress1", "shoe1"])], AVAILABLE)
    assert [p["outfit_id"] for p in validated] == ["o1"]


def test_rejects_missing_footwear() -> None:
    validated, notes = validate_proposals(
        [_proposal("o1", ["top1", "bottom1"])], AVAILABLE
    )
    assert validated == []
    assert any("品类不完整" in note for note in notes)


def test_rejects_one_piece_conflicting_with_separates() -> None:
    validated, notes = validate_proposals(
        [_proposal("o1", ["dress1", "top1", "shoe1"])], AVAILABLE
    )
    assert validated == []
    assert any("品类不完整" in note for note in notes)


def test_rejects_out_of_pool_item() -> None:
    validated, notes = validate_proposals(
        [_proposal("o1", ["top1", "bottom1", "shoe1", "ghost"])], AVAILABLE
    )
    assert validated == []
    assert any("候选池外" in note for note in notes)


def test_rejects_duplicate_items() -> None:
    validated, notes = validate_proposals(
        [_proposal("o1", ["top1", "top1", "bottom1", "shoe1"])], AVAILABLE
    )
    assert validated == []
    assert any("重复" in note for note in notes)


def test_rejects_empty_item_ids() -> None:
    validated, notes = validate_proposals([_proposal("o1", [])], AVAILABLE)
    assert validated == []
    assert any("没有单品" in note for note in notes)


def test_mixed_proposals_keep_only_valid() -> None:
    validated, _ = validate_proposals(
        [
            _proposal("o1", ["top1", "bottom1", "shoe1"]),
            _proposal("o2", ["top1", "bottom1"]),
        ],
        AVAILABLE,
    )
    assert [p["outfit_id"] for p in validated] == ["o1"]
