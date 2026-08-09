"""Evaluate the deterministic v3.3 Task Router on fixed cases."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
APPS_API_ROOT = WORKSPACE_ROOT / "apps" / "api"
if str(APPS_API_ROOT) not in sys.path:
    sys.path.insert(0, str(APPS_API_ROOT))

from styleforge.common.console import configure_utf8_console  # noqa: E402
from styleforge.common.files import write_json_atomic  # noqa: E402
from styleforge.orchestration.task_router import TaskRouter, TaskType  # noqa: E402


DEFAULT_CASES = Path(__file__).resolve().parents[1] / "cases" / "task_routing.json"
DEFAULT_REPORT = WORKSPACE_ROOT / "artifacts" / "evaluation" / "task_routing_baseline.json"


def load_cases(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("task routing cases must be a non-empty JSON array")

    seen_ids: set[str] = set()
    cases: list[dict[str, Any]] = []
    for index, raw_case in enumerate(payload, start=1):
        if not isinstance(raw_case, dict):
            raise ValueError(f"case {index} must be a JSON object")
        case_id = str(raw_case.get("id", "")).strip()
        request = str(raw_case.get("request", "")).strip()
        expected = str(raw_case.get("expected_task_type", "")).strip()
        if not case_id or case_id in seen_ids:
            raise ValueError(f"case {index} has an empty or duplicate id: {case_id!r}")
        if not request:
            raise ValueError(f"case {case_id} has an empty request")
        TaskType(expected)
        seen_ids.add(case_id)
        cases.append({**raw_case, "id": case_id, "request": request})
    return cases


def evaluate_task_routing(
    cases_path: Path = DEFAULT_CASES,
    *,
    router: TaskRouter | None = None,
) -> dict[str, Any]:
    cases = load_cases(cases_path)
    router = router or TaskRouter()
    expected_counts: Counter[str] = Counter()
    correct_counts: Counter[str] = Counter()
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    failures: list[dict[str, Any]] = []

    for case in cases:
        expected = TaskType(case["expected_task_type"])
        route = router.route(
            case["request"],
            current_outfit_id=str(case.get("current_outfit_id", "")),
            has_candidate_item=bool(case.get("has_candidate_item", False)),
            requested_task_type=case.get("requested_task_type"),
        )
        expected_counts[expected.value] += 1
        confusion[expected.value][route.task_type.value] += 1
        if route.task_type is expected:
            correct_counts[expected.value] += 1
            continue
        failures.append(
            {
                "id": case["id"],
                "request": case["request"],
                "expected": expected.value,
                "actual": route.task_type.value,
                "reason": route.reason,
            }
        )

    correct = len(cases) - len(failures)
    per_class_accuracy = {
        task_type.value: round(
            correct_counts[task_type.value] / expected_counts[task_type.value],
            6,
        )
        for task_type in TaskType
        if expected_counts[task_type.value]
    }
    return {
        "schema_version": "styleforge.task-routing-eval.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "router_version": "deterministic-p2-v1",
        "cases_path": str(cases_path.resolve()),
        "case_count": len(cases),
        "correct_count": correct,
        "metrics": {
            "accuracy": round(correct / len(cases), 6),
            "per_class_accuracy": per_class_accuracy,
        },
        "class_distribution": dict(sorted(expected_counts.items())),
        "confusion_matrix": {
            expected: dict(sorted(actual_counts.items()))
            for expected, actual_counts in sorted(confusion.items())
        },
        "failures": failures,
        "limitations": [
            "Fixed intent-routing benchmark; it does not measure outfit quality.",
            "Cases are curated and do not yet represent real production traffic.",
            "Five-dimension outfit scoring must be evaluated in a separate benchmark.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser


def main() -> None:
    configure_utf8_console()
    args = build_parser().parse_args()
    report = evaluate_task_routing(args.cases)
    write_json_atomic(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
