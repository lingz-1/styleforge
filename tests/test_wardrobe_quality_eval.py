from __future__ import annotations

from collections import Counter
from pathlib import Path

from evals.quality_benchmark import load_quality_cases
from evals.runners.evaluate_wardrobe_quality import (
    _item_fact_text,
    evaluate_benchmark,
    evaluate_case,
)
from evals.wardrobe_fixtures import fixture_item_id, load_fixture
from styleforge.orchestration.task_router import TaskRouter


SYSTEM_CASES = Path(__file__).resolve().parent.parent / "evals/cases/system_quality_v2.json"


DIMENSIONS = {
    "request_relevance": 9,
    "request_specificity": 9,
    "outfit_coordination": 9,
    "wearability": 9,
    "freshness": 9,
}


def test_item_fact_text_includes_conservative_inferred_features() -> None:
    fixture = load_fixture("real_formal_separates")
    loafers = next(item for item in fixture.items if "Loafers" in item.name)

    fact = _item_fact_text(loafers)

    assert "comfortable" in fact
    assert "formal" in fact


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


def test_system_quality_v2_freezes_forty_two_layered_real_cases() -> None:
    cases = load_quality_cases(SYSTEM_CASES)

    assert len(cases) == 42
    assert sum(case.split == "validation" for case in cases) == 26
    assert sum(case.split == "holdout" for case in cases) == 16
    assert Counter(case.evaluation_group for case in cases) == {
        "core": 24,
        "robustness": 8,
        "rag": 6,
        "api_e2e": 4,
    }
    assert Counter(case.execution_adapter for case in cases) == {
        "workflow": 32,
        "retrieval": 6,
        "api": 4,
    }
    assert all(case.execution_modes == ("configured",) for case in cases)
    assert all(not case.requested_task_type for case in cases)
    assert {case.expected_route for case in cases} == {
        "outfit_recommend",
        "outfit_modify",
        "item_advice",
        "style_advice",
        "wardrobe_compatibility",
        "wardrobe_gap",
        "wardrobe_retrieval",
        "api_isolation",
    }
    router = TaskRouter()
    for case in cases:
        if case.execution_adapter != "workflow":
            continue
        route = router.route(
            case.request,
            current_outfit_id=case.current_outfit_id,
            has_candidate_item=case.candidate_item is not None,
        )
        assert route.task_type.value == case.expected_route, case.case_id
        for turn in case.pre_turns:
            turn_route = router.route(turn.request)
            assert turn_route.task_type.value == turn.expected_route, (
                case.case_id,
                turn.request,
            )


def test_v2_retrieval_case_checks_hit_mode_and_allowlist() -> None:
    case = next(
        case
        for case in load_quality_cases(SYSTEM_CASES)
        if case.case_id == "RAG-V01"
    )
    fixture = load_fixture(case.fixture)
    payload = {
        "task_type": "wardrobe_retrieval",
        "status": "completed",
        "result": {
            "retrieved_item_ids": [fixture_item_id("polyvore_raw_101427198")],
            "mode": "keyword",
            "semantic_available": False,
        },
    }

    result = evaluate_case(case, payload, fixture)

    assert result["passed"] is True
    payload["result"]["retrieved_item_ids"] = ["foreign-item"]
    failed = evaluate_case(case, payload, fixture)
    assert "retrieval_allowlist_violation" in failed["issue_categories"]
    assert "required_retrieval_missing" in failed["issue_categories"]


def test_v2_api_case_requires_all_endpoint_checks() -> None:
    case = next(
        case
        for case in load_quality_cases(SYSTEM_CASES)
        if case.case_id == "API-H01"
    )
    fixture = load_fixture(case.fixture)
    payload = {
        "task_type": "api_isolation",
        "status": "completed",
        "result": {},
        "_api_checks": {
            "session_created": True,
            "foreign_user_denied": True,
            "owner_can_read": True,
        },
        "_wardrobe_before": list(fixture.item_ids),
        "_wardrobe_after": list(fixture.item_ids),
    }

    assert evaluate_case(case, payload, fixture)["passed"] is True
    payload["_api_checks"]["foreign_user_denied"] = False
    failed = evaluate_case(case, payload, fixture)
    assert "api_contract_failed" in failed["issue_categories"]


def test_v2_security_case_checks_runtime_signal_and_zero_wardrobe_mutation() -> None:
    case = next(case for case in load_quality_cases(SYSTEM_CASES) if case.case_id == "H-C02")
    fixture = load_fixture(case.fixture)
    payload = {
        "task_type": "wardrobe_compatibility",
        "status": "completed",
        "result": {
            "candidate_item": case.candidate_item,
            "compatibility_score": 50,
            "recommendation": "consider",
            "evidence": [],
        },
        "_duration_ms": 100,
        "_wardrobe_before": list(fixture.item_ids),
        "_wardrobe_after": list(fixture.item_ids),
        "_security_signal_detected": True,
        "_security_categories": [
            "hierarchy_override",
            "secret_exfiltration",
            "tool_coercion",
        ],
        "_independent_judge": {"profile": "task", "overall": 80},
    }

    result = evaluate_case(case, payload, fixture)

    assert result["passed"] is True
    assert result["security_signal_detected"] is True
    assert result["wardrobe_unchanged"] is True


def test_v2_multiturn_case_preserves_latest_non_target_items() -> None:
    case = next(case for case in load_quality_cases(SYSTEM_CASES) if case.case_id == "H-M01")
    fixture = load_fixture(case.fixture)
    keys = {
        "top": "polyvore_raw_207224296",
        "bottom": "polyvore_raw_208592546",
        "footwear": "polyvore_raw_193817407",
        "old_outerwear": "polyvore_raw_200002806",
        "new_outerwear": "polyvore_raw_147932697",
    }
    latest_ids = [fixture_item_id(keys[name]) for name in ("top", "bottom", "footwear", "old_outerwear")]
    current_ids = [fixture_item_id(keys[name]) for name in ("top", "bottom", "footwear", "new_outerwear")]
    payload = {
        "task_type": "outfit_modify",
        "status": "completed",
        "result": {"alternatives": [{"item_ids": current_ids}]},
        "_pre_turn_results": [
            {"task_type": "outfit_recommend", "status": "completed", "result": {"recommendations": [{"item_ids": latest_ids}]}},
            {"task_type": "outfit_modify", "status": "completed", "result": {"alternatives": [{"item_ids": latest_ids}]}},
        ],
        "_duration_ms": 100,
        "_independent_judge": {"profile": "outfit", "overall": 80},
    }

    result = evaluate_case(case, payload, fixture)

    assert result["passed"] is True
