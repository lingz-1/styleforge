from __future__ import annotations

from evals.quality_benchmark import load_quality_cases
from evals.runners.evaluate_wardrobe_quality import evaluate_benchmark, evaluate_case
from evals.wardrobe_fixtures import fixture_item_id, load_fixture


DIMENSIONS = {
    "request_relevance": 9,
    "request_specificity": 9,
    "outfit_coordination": 9,
    "wearability": 9,
    "freshness": 9,
}


def _wedding_payload(*, complete: bool = True) -> dict:
    keys = ["lace_dress", "nude_pumps", "black_blazer"]
    if complete:
        keys.append("pearl_earrings")
    return {
        "task_type": "item_advice",
        "status": "completed",
        "result": {
            "anchor_item": {"item_id": fixture_item_id("lace_dress")},
            "compatible_items_by_slot": {"footwear": [fixture_item_id("nude_pumps")]},
            "sample_outfits": [
                {
                    "item_ids": [fixture_item_id(key) for key in keys],
                    "dimension_scores": DIMENSIONS,
                }
            ]
        },
    }


def test_evaluate_case_accepts_complete_one_piece_result() -> None:
    case = next(
        case for case in load_quality_cases() if case.case_id == "item-wedding-046"
    )
    result = evaluate_case(case, _wedding_payload(), load_fixture(case.fixture))
    assert result["passed"] is True
    assert result["issues"] == []
    assert result["top_slots"] == ["accessory", "footwear", "one_piece", "outerwear"]


def test_evaluate_case_exposes_ext_002_missing_accessory() -> None:
    case = next(
        case for case in load_quality_cases() if case.case_id == "item-wedding-046"
    )
    result = evaluate_case(
        case,
        _wedding_payload(complete=False),
        load_fixture(case.fixture),
    )
    assert result["passed"] is False
    assert "required_slot_missing" in result["issue_categories"]
    assert any("accessory" in issue["detail"] for issue in result["issues"])


def test_evaluate_case_rejects_foreign_and_forbidden_items() -> None:
    case = next(
        case for case in load_quality_cases() if case.case_id == "commute-minimal-002"
    )
    payload = {
        "task_type": "outfit_recommend",
        "status": "completed",
        "result": {
            "recommendations": [
                {
                    "item_ids": [
                        fixture_item_id("white_shirt"),
                        fixture_item_id("black_trousers"),
                        fixture_item_id("black_stilettos"),
                        "00000000-0000-0000-0000-000000000001",
                    ],
                    "dimension_scores": DIMENSIONS,
                }
            ]
        },
    }
    result = evaluate_case(case, payload, load_fixture(case.fixture))
    assert {"foreign_item", "forbidden_item_present"} <= set(result["issue_categories"])


def test_evaluate_case_labels_no_llm_payload_as_deterministic() -> None:
    case = next(
        case for case in load_quality_cases() if case.case_id == "commute-client-001"
    )
    payload = {
        "task_type": "outfit_recommend",
        "status": "completed",
        "llm_enabled": False,
        "result": {
            "recommendations": [
                {
                    "item_ids": [
                        fixture_item_id("white_shirt"),
                        fixture_item_id("black_trousers"),
                        fixture_item_id("black_loafers"),
                    ]
                }
            ]
        },
    }

    result = evaluate_case(case, payload, load_fixture(case.fixture))

    assert result["generation_mode"] == "deterministic"
    assert result["runtime_debug"]["candidate_count"] == 0


def test_evaluate_case_preserves_safe_extension_validation_diagnostics() -> None:
    case = next(
        case for case in load_quality_cases() if case.case_id == "style-business-casual-049"
    )
    payload = {
        "run_id": "run-debug",
        "task_type": "style_advice",
        "status": "needs_clarification",
        "result": {"title": "Business casual", "summary": "需要补充信息"},
        "agentic_outcome": {
            "status": "ended",
            "extension_validation_failures": ["principles field required"],
        },
    }

    result = evaluate_case(case, payload, load_fixture(case.fixture))

    assert result["run_id"] == "run-debug"
    assert result["runtime_debug"]["agentic_status"] == "ended"
    assert result["runtime_debug"]["extension_validation_failures"] == [
        "principles field required"
    ]


def test_evaluate_benchmark_aggregates_failures() -> None:
    def executor(case, fixture):
        if case.case_id == "item-wedding-046":
            return _wedding_payload(complete=False)
        return {
            "task_type": case.expected_route,
            "status": "execution_error",
            "result": {},
        }

    report = evaluate_benchmark(
        executor,
        selected_ids={"item-wedding-046", "no-solution-daily-036"},
    )
    assert report["case_count"] == 2
    assert report["passed_count"] == 0
    assert report["metrics"]["route_accuracy"] == 1.0
    assert report["issue_counts"]["required_slot_missing"] == 1
    assert report["issue_counts"]["status_mismatch"] == 1


def test_evaluate_benchmark_filters_lightweight_mode_and_groups_metrics() -> None:
    seen: list[str] = []

    def executor(case, fixture):
        seen.append(case.case_id)
        return {
            "task_type": case.expected_route,
            "status": case.expected_status,
            "result": {"recommendations": []},
        }

    report = evaluate_benchmark(executor, execution_mode="deterministic")

    assert report["case_count"] == 42
    assert len(seen) == 42
    assert set(report["metrics_by_route"]) == {"outfit_recommend"}
    assert report["metrics_by_route"]["outfit_recommend"]["case_count"] == 42
