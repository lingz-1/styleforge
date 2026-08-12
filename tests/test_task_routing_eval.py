from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.runners.evaluate_task_routing import evaluate_task_routing, load_cases
from styleforge.orchestration.task_router import TaskType


CASES_PATH = Path("evals/cases/task_routing.json")


def test_fixed_routing_cases_are_balanced_and_cover_every_task() -> None:
    cases = load_cases(CASES_PATH)
    counts = {
        task_type: sum(
            case["expected_task_type"] == task_type.value for case in cases
        )
        for task_type in TaskType
    }

    assert len(cases) == 60
    assert set(counts.values()) == {10}


def test_task_routing_baseline_has_no_known_case_failures() -> None:
    report = evaluate_task_routing(CASES_PATH)

    assert report["case_count"] == 60
    assert report["metrics"]["accuracy"] == 1.0
    assert report["failures"] == []


def test_case_loader_rejects_duplicate_ids(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            [
                {
                    "id": "duplicate",
                    "request": "给我推荐一套",
                    "expected_task_type": "outfit_recommend",
                },
                {
                    "id": "duplicate",
                    "request": "鞋换一双",
                    "expected_task_type": "outfit_modify",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate id"):
        load_cases(cases_path)
