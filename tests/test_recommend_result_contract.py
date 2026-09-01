from styleforge.services.recommend_result_contract import (
    OUTFIT_RECOMMEND_RESULT_SCHEMA,
    normalize_outfit_recommend_result,
)


def test_normalizes_deterministic_flat_result() -> None:
    recommendations = [{"outfit_id": "local-1", "item_ids": ["top", "pants"]}]

    normalized = normalize_outfit_recommend_result(
        {
            "run_id": "engine-run",
            "status": "completed",
            "recommendations": recommendations,
            "diagnostics": {"generated_candidate_count": 1},
        },
        run_id="task-run",
    )

    assert normalized["schema_version"] == OUTFIT_RECOMMEND_RESULT_SCHEMA
    assert normalized["run_id"] == "task-run"
    assert normalized["status"] == "completed"
    assert normalized["structured_result"] == {
        "run_id": "task-run",
        "status": "completed",
        "recommendations": recommendations,
    }
    assert normalized["recommendations"] == recommendations
    assert normalized["diagnostics"] == {"generated_candidate_count": 1}


def test_normalizes_agentic_structured_result_and_replaces_divergent_alias() -> None:
    canonical = [{"outfit_id": "agentic-1", "item_ids": ["top", "skirt"]}]

    normalized = normalize_outfit_recommend_result(
        {
            "status": "completed",
            "structured_result": {
                "run_id": "",
                "status": "completed",
                "recommendations": canonical,
            },
            "recommendations": [{"outfit_id": "stale"}],
        },
        run_id="task-run",
    )

    assert normalized["structured_result"]["recommendations"] == canonical
    assert normalized["recommendations"] == canonical
    assert normalized["structured_result"]["run_id"] == "task-run"


def test_normalizes_missing_recommendations_for_terminal_result() -> None:
    normalized = normalize_outfit_recommend_result(
        {"message": "需要补充场合"},
        run_id="task-run",
        status="needs_clarification",
    )

    assert normalized["status"] == "needs_clarification"
    assert normalized["structured_result"]["status"] == "needs_clarification"
    assert normalized["structured_result"]["recommendations"] == []
    assert normalized["recommendations"] == []
