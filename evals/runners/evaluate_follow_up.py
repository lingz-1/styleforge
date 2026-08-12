"""Evaluate the deterministic follow-up detector on fixed cases.

``is_follow_up`` decides whether a short request is an adjustment to the current
outfit (e.g. ``更正式一点``) or a fresh brief (``明天面试穿什么``). It drives the
two-pass routing rewrite in the multi-turn dialogue, so a wrong label is either
a forced re-route of a fresh request or a missed follow-up. This eval measures
how well the detector matches an intent-labeled corpus.

The detector is pure and deterministic, so the metrics are unit-testable
without a provider; the CLI only writes the report artifact.
"""

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
from styleforge.workflow.task_workflow import is_follow_up  # noqa: E402


DEFAULT_CASES = Path(__file__).resolve().parents[1] / "cases" / "follow_up.json"
DEFAULT_REPORT = WORKSPACE_ROOT / "artifacts" / "evaluation" / "follow_up.json"

_VALID_LABELS = {"follow_up", "fresh"}


def load_cases(path: Path) -> list[dict[str, Any]]:
    """Validate the case file: unique ids, non-empty requests, valid labels."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("follow-up cases must be a non-empty JSON array")

    seen_ids: set[str] = set()
    cases: list[dict[str, Any]] = []
    for index, raw_case in enumerate(payload, start=1):
        if not isinstance(raw_case, dict):
            raise ValueError(f"case {index} must be a JSON object")
        case_id = str(raw_case.get("id", "")).strip()
        request = str(raw_case.get("request", "")).strip()
        expected = str(raw_case.get("expected", "")).strip()
        if not case_id or case_id in seen_ids:
            raise ValueError(f"case {index} has an empty or duplicate id: {case_id!r}")
        if not request:
            raise ValueError(f"case {case_id} has an empty request")
        if expected not in _VALID_LABELS:
            raise ValueError(f"case {case_id} has an invalid expected label: {expected!r}")
        seen_ids.add(case_id)
        cases.append({**raw_case, "id": case_id, "request": request, "expected": expected})
    return cases


def evaluate_follow_up(cases_path: Path = DEFAULT_CASES) -> dict[str, Any]:
    """Run the detector over every case and aggregate agreement + failure modes.

    Cases annotated ``known_gap: true`` are the detector's documented blind
    spots (e.g. addition signals like ``加一顶帽子``). They are kept out of the
    main accuracy metric and reported separately so the regression gate stays
    strict while the limits stay visible.
    """
    cases = load_cases(cases_path)
    main_cases = [c for c in cases if not c.get("known_gap")]
    hard_cases = [c for c in cases if c.get("known_gap")]

    expected_counts: Counter[str] = Counter()
    correct_counts: Counter[str] = Counter()
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    failures: list[dict[str, Any]] = []

    for case in main_cases:
        expected = case["expected"]
        actual = "follow_up" if is_follow_up(case["request"]) else "fresh"
        expected_counts[expected] += 1
        confusion[expected][actual] += 1
        if actual == expected:
            correct_counts[expected] += 1
            continue
        failures.append(
            {
                "id": case["id"],
                "request": case["request"],
                "rule": case.get("rule", ""),
                "expected": expected,
                "actual": actual,
            }
        )

    known_gaps: list[dict[str, Any]] = []
    for case in hard_cases:
        actual = "follow_up" if is_follow_up(case["request"]) else "fresh"
        known_gaps.append(
            {
                "id": case["id"],
                "request": case["request"],
                "rule": case.get("rule", ""),
                "expected": case["expected"],
                "actual": actual,
            }
        )

    correct = len(main_cases) - len(failures)
    false_positive_follow_up = confusion["fresh"]["follow_up"]
    missed_follow_up = confusion["follow_up"]["fresh"]
    per_class_accuracy = {
        label: round(correct_counts[label] / expected_counts[label], 6)
        for label in _VALID_LABELS
        if expected_counts[label]
    }
    return {
        "schema_version": "styleforge.follow-up-eval.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "detector": "is_follow_up@task_workflow",
        "cases_path": str(cases_path.resolve()),
        "case_count": len(cases),
        "correct_count": correct,
        "metrics": {
            "accuracy": round(correct / len(main_cases), 6),
            "per_class_accuracy": per_class_accuracy,
            "false_positive_follow_up_rate": round(
                false_positive_follow_up / expected_counts["fresh"], 6
            )
            if expected_counts["fresh"]
            else 0.0,
            "missed_follow_up_rate": round(
                missed_follow_up / expected_counts["follow_up"], 6
            )
            if expected_counts["follow_up"]
            else 0.0,
        },
        "label_distribution": dict(sorted(expected_counts.items())),
        "confusion_matrix": {
            expected: dict(sorted(actual_counts.items()))
            for expected, actual_counts in sorted(confusion.items())
        },
        "failures": failures,
        "known_gap_count": len(known_gaps),
        "known_gaps": known_gaps,
        "limitations": [
            "Curated Chinese corpus; not yet sampled from production traffic.",
            "Labels are the annotator's intent judgment; ambiguity is documented per rule.",
            "known_gap cases are excluded from accuracy and listed under known_gaps.",
            "This measures the routing predicate only, not the full modify execution.",
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
    report = evaluate_follow_up(args.cases)
    write_json_atomic(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
