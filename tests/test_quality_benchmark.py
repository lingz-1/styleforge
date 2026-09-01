from __future__ import annotations

from collections import Counter
from pathlib import Path

from evals.quality_benchmark import SCORE_DIMENSIONS, load_quality_cases


def test_quality_benchmark_contract_is_valid_and_balanced() -> None:
    cases = load_quality_cases()
    assert len(cases) == 60
    assert len({case.case_id for case in cases}) == len(cases)
    assert Counter(case.fixture for case in cases) == {
        "commute": 15,
        "rain": 12,
        "sport": 8,
        "limited": 14,
        "no_solution": 8,
        "one_piece": 3,
    }
    assert Counter(case.expected_route for case in cases) == {
        "outfit_recommend": 45,
        "item_advice": 3,
        "style_advice": 3,
        "wardrobe_gap": 3,
        "wardrobe_compatibility": 3,
        "outfit_modify": 3,
    }
    assert sum("deterministic" in case.execution_modes for case in cases) == 42
    assert sum("configured" in case.execution_modes for case in cases) == 18


def test_scored_cases_use_the_complete_five_dimension_rubric() -> None:
    cases = load_quality_cases()
    scored = [case for case in cases if case.min_scores]
    assert len(scored) == 3
    for case in scored:
        assert set(case.min_scores) == SCORE_DIMENSIONS
        assert all(1 <= score <= 10 for score in case.min_scores.values())


def test_ext_002_cases_lock_one_piece_completeness() -> None:
    cases = {case.case_id: case for case in load_quality_cases()}
    wedding = cases["item-wedding-046"]
    assert wedding.anchor_item_key == "lace_dress"
    assert {"one_piece", "footwear", "outerwear", "accessory"} <= set(
        wedding.required_slots
    )
    date = cases["item-date-047"]
    assert date.anchor_item_key == "slip_dress"
    assert {"one_piece", "footwear", "outerwear"} <= set(date.required_slots)


def test_real_ext_002_cases_use_polyvore_fixture() -> None:
    path = Path("evals/cases/wardrobe_quality_real.json")
    cases = {case.case_id: case for case in load_quality_cases(path)}
    assert set(cases) == {
        "real-one-piece-wedding-001",
        "real-one-piece-date-002",
        "real-recommend-wedding-003",
        "real-style-evening-004",
        "real-gap-versatility-005",
        "real-compatibility-grey-shoes-006",
        "real-modify-date-shoes-007",
    }
    assert {case.fixture for case in cases.values()} == {"polyvore_one_piece"}
    assert all(case.execution_modes == ("configured",) for case in cases.values())
    assert cases["real-recommend-wedding-003"].max_results == 1
    assert {case.expected_route for case in cases.values()} == {
        "outfit_recommend",
        "outfit_modify",
        "style_advice",
        "item_advice",
        "wardrobe_compatibility",
        "wardrobe_gap",
    }
    assert cases["real-one-piece-wedding-001"].anchor_item_key == "polyvore_201181532"
    assert {"one_piece", "footwear", "accessory"} <= set(
        cases["real-one-piece-wedding-001"].required_slots
    )
    assert cases["real-one-piece-date-002"].anchor_item_key == "polyvore_151616863"
    assert {"one_piece", "footwear", "outerwear"} <= set(
        cases["real-one-piece-date-002"].required_slots
    )
    assert (
        cases["real-compatibility-grey-shoes-006"].candidate_item["item_id"]
        == "107132140"
    )


def test_failure_cases_require_explicit_non_success_contracts() -> None:
    cases = {case.case_id: case for case in load_quality_cases()}
    no_solution = cases["no-solution-daily-036"]
    assert no_solution.expected_status == "infeasible"
    assert no_solution.required_empty_recommendations is True
    gap = cases["gap-no-solution-053"]
    assert set(gap.expected_missing_slots) == {"top", "bottom", "footwear"}


def test_configured_cases_cover_all_six_task_contracts() -> None:
    configured = [
        case for case in load_quality_cases() if "configured" in case.execution_modes
    ]
    assert Counter(case.expected_route for case in configured) == {
        "outfit_recommend": 3,
        "outfit_modify": 3,
        "style_advice": 3,
        "item_advice": 3,
        "wardrobe_compatibility": 3,
        "wardrobe_gap": 3,
    }
    assert all(case.required_result_fields for case in configured if case.expected_route != "outfit_recommend")
