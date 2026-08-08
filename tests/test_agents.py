from styleforge.agents.reviewer import ReviewerAgent
from styleforge.agents.stylist import StylistAgent
from styleforge.core.schemas import OutfitCandidate, TaskSpec


def _candidate(outfit_id: str, item_ids: tuple[str, ...], score: float) -> OutfitCandidate:
    return OutfitCandidate(
        outfit_id=outfit_id,
        item_ids=item_ids,
        slot_items={"top": item_ids[0], "bottom": item_ids[1]},
        hard_valid=True,
        score=score,
    )


def test_stylist_only_selects_legal_candidates() -> None:
    task = TaskSpec(user_id="user", max_results=1)
    legal = _candidate("legal", ("top-1", "bottom-1"), 80.0)
    illegal = OutfitCandidate(
        outfit_id="illegal",
        item_ids=("top-2", "bottom-2"),
        slot_items={"top": "top-2", "bottom": "bottom-2"},
        hard_valid=False,
        score=100.0,
    )

    selected = StylistAgent().select([illegal, legal], task)

    assert [candidate.outfit_id for candidate in selected] == ["legal"]


def test_reviewer_rejects_items_outside_wardrobe() -> None:
    task = TaskSpec(user_id="user")
    candidate = _candidate("candidate", ("top-1", "bottom-1"), 80.0)

    decision = ReviewerAgent().review([candidate], task, {"top-1"})

    assert decision.accepted is False
    assert "非用户衣柜" in decision.notes[0]


def test_stylist_prefers_disjoint_outfits() -> None:
    task = TaskSpec(user_id="user", max_results=2)
    best = _candidate("best", ("top-1", "bottom-1"), 90.0)
    overlapping = _candidate("overlap", ("top-1", "bottom-2"), 89.0)
    disjoint = _candidate("disjoint", ("top-2", "bottom-3"), 88.0)

    selected = StylistAgent().select([best, overlapping, disjoint], task)

    assert [candidate.outfit_id for candidate in selected] == ["best", "disjoint"]
