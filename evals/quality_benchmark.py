"""Contract loader for the deterministic wardrobe-quality benchmark."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.wardrobe_fixtures import load_fixture


DEFAULT_CASES_PATH = Path(__file__).with_name("cases") / "wardrobe_quality.json"
SCORE_DIMENSIONS = {
    "request_relevance",
    "request_specificity",
    "outfit_coordination",
    "wearability",
    "freshness",
}


@dataclass(frozen=True)
class QualityBenchmarkCase:
    case_id: str
    fixture: str
    request: str
    expected_route: str
    expected_status: str
    execution_modes: tuple[str, ...] = ("deterministic", "configured")
    requested_task_type: str = ""
    anchor_item_key: str = ""
    current_outfit_id: str = ""
    current_item_keys: tuple[str, ...] = ()
    target_slot: str = ""
    candidate_item: dict[str, Any] | None = None
    required_item_keys: tuple[str, ...] = ()
    forbidden_item_keys: tuple[str, ...] = ()
    required_slots: tuple[str, ...] = ()
    required_features: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = ()
    expected_missing_elements: tuple[str, ...] = ()
    expected_missing_slots: tuple[str, ...] = ()
    expected_zero_slots: tuple[str, ...] = ()
    min_gap_count: int = 0
    required_empty_recommendations: bool = False
    required_result_fields: tuple[str, ...] = ()
    min_outfit_count: int = 0
    max_results: int = 3
    min_scores: dict[str, int] = field(default_factory=dict)


def _strings(raw: dict[str, Any], key: str) -> tuple[str, ...]:
    return tuple(str(value) for value in raw.get(key) or ())


def _validate_case(case: QualityBenchmarkCase) -> None:
    fixture = load_fixture(case.fixture)
    fixture_keys = set(fixture.item_keys)
    referenced = {
        *case.required_item_keys,
        *case.forbidden_item_keys,
        *case.current_item_keys,
        *([case.anchor_item_key] if case.anchor_item_key else []),
    }
    unknown = referenced - fixture_keys
    if unknown:
        raise ValueError(f"Case {case.case_id!r} references unknown fixture items: {unknown}")
    score_keys = set(case.min_scores)
    if score_keys and score_keys != SCORE_DIMENSIONS:
        raise ValueError(f"Case {case.case_id!r} must define all five score dimensions")
    if any(score < 1 or score > 10 for score in case.min_scores.values()):
        raise ValueError(f"Case {case.case_id!r} contains a score outside 1..10")
    if not case.execution_modes or not set(case.execution_modes) <= {
        "deterministic",
        "configured",
    }:
        raise ValueError(f"Case {case.case_id!r} has invalid execution_modes")
    if case.requested_task_type and case.requested_task_type != case.expected_route:
        raise ValueError(
            f"Case {case.case_id!r} requested_task_type must match expected_route"
        )
    if case.min_outfit_count < 0:
        raise ValueError(f"Case {case.case_id!r} has negative min_outfit_count")
    if case.min_gap_count < 0:
        raise ValueError(f"Case {case.case_id!r} has negative min_gap_count")
    if case.max_results < 1 or case.max_results > 10:
        raise ValueError(f"Case {case.case_id!r} max_results must be within 1..10")
    if case.expected_status == "completed" and case.expected_route == "outfit_recommend":
        if not case.required_slots:
            raise ValueError(f"Completed recommendation {case.case_id!r} needs required_slots")
    if case.expected_status == "infeasible" and not case.required_empty_recommendations:
        raise ValueError(f"Infeasible case {case.case_id!r} must require empty recommendations")


def load_quality_cases(path: Path = DEFAULT_CASES_PATH) -> tuple[QualityBenchmarkCase, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "styleforge.wardrobe-quality-benchmark.v1":
        raise ValueError("Unsupported wardrobe quality benchmark schema")
    if set(payload.get("score_dimensions") or ()) != SCORE_DIMENSIONS:
        raise ValueError("Benchmark score dimensions do not match the StyleForge rubric")
    cases: list[QualityBenchmarkCase] = []
    seen: set[str] = set()
    for raw in payload.get("cases") or ():
        case_id = str(raw.get("id") or "").strip()
        if not case_id or case_id in seen:
            raise ValueError(f"Invalid or duplicate quality case id: {case_id!r}")
        seen.add(case_id)
        case = QualityBenchmarkCase(
            case_id=case_id,
            fixture=str(raw["fixture"]),
            request=str(raw["request"]),
            expected_route=str(raw["expected_route"]),
            expected_status=str(raw["expected_status"]),
            execution_modes=_strings(raw, "execution_modes")
            or ("deterministic", "configured"),
            requested_task_type=str(raw.get("requested_task_type") or ""),
            anchor_item_key=str(raw.get("anchor_item_key") or ""),
            current_outfit_id=str(raw.get("current_outfit_id") or ""),
            current_item_keys=_strings(raw, "current_item_keys"),
            target_slot=str(raw.get("target_slot") or ""),
            candidate_item=(
                dict(raw["candidate_item"])
                if isinstance(raw.get("candidate_item"), dict)
                else None
            ),
            required_item_keys=_strings(raw, "required_item_keys"),
            forbidden_item_keys=_strings(raw, "forbidden_item_keys"),
            required_slots=_strings(raw, "required_slots"),
            required_features=_strings(raw, "required_features"),
            forbidden_claims=_strings(raw, "forbidden_claims"),
            expected_missing_elements=_strings(raw, "expected_missing_elements"),
            expected_missing_slots=_strings(raw, "expected_missing_slots"),
            expected_zero_slots=_strings(raw, "expected_zero_slots"),
            min_gap_count=int(raw.get("min_gap_count") or 0),
            required_empty_recommendations=bool(raw.get("required_empty_recommendations")),
            required_result_fields=_strings(raw, "required_result_fields"),
            min_outfit_count=int(raw.get("min_outfit_count") or 0),
            max_results=int(raw.get("max_results") or 3),
            min_scores={str(key): int(value) for key, value in (raw.get("min_scores") or {}).items()},
        )
        _validate_case(case)
        cases.append(case)
    return tuple(cases)
