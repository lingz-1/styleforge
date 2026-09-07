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
SUPPORTED_SCHEMA_VERSIONS = {
    "styleforge.wardrobe-quality-benchmark.v1",
    "styleforge.wardrobe-quality-benchmark.v2",
}


@dataclass(frozen=True)
class BenchmarkTurn:
    request: str
    expected_route: str = ""
    expected_status: str = "completed"
    requested_task_type: str = ""
    max_results: int = 1


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
    required_slot_alternatives: tuple[tuple[str, ...], ...] = ()
    required_features: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = ()
    expected_missing_elements: tuple[str, ...] = ()
    expected_missing_slots: tuple[str, ...] = ()
    forbidden_missing_slots: tuple[str, ...] = ()
    expected_zero_slots: tuple[str, ...] = ()
    min_gap_count: int = 0
    required_empty_recommendations: bool = False
    required_result_fields: tuple[str, ...] = ()
    min_outfit_count: int = 0
    max_results: int = 3
    min_scores: dict[str, int] = field(default_factory=dict)
    split: str = "validation"
    evaluation_group: str = "core"
    execution_adapter: str = "workflow"
    pre_turns: tuple[BenchmarkTurn, ...] = ()
    preseed_preferences: tuple[dict[str, Any], ...] = ()
    require_wardrobe_unchanged: bool = False
    require_preferences_unchanged: bool = False
    require_security_signal: bool = False
    expected_security_categories: tuple[str, ...] = ()
    forbidden_output_fragments: tuple[str, ...] = ()
    required_output_fragments: tuple[str, ...] = ()
    preserve_latest_except_slot: str = ""
    max_duration_ms: int = 0
    min_llm_calls: int = 0
    max_llm_calls: int = 0
    min_judge_overall: float = 0.0
    retrieval_query: str = ""
    retrieval_limit: int = 5
    required_retrieved_item_keys: tuple[str, ...] = ()
    any_retrieved_item_keys: tuple[str, ...] = ()
    expected_retrieval_modes: tuple[str, ...] = ()
    require_semantic_retrieval: bool = False
    force_semantic_unavailable: bool = False
    api_scenario: str = ""

    @property
    def suite(self) -> str:
        """Backward-compatible alias for older report consumers."""
        return self.split


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
    if case.split not in {"validation", "holdout"}:
        raise ValueError(f"Case {case.case_id!r} has invalid split {case.split!r}")
    if case.evaluation_group not in {"core", "robustness", "rag", "api_e2e"}:
        raise ValueError(
            f"Case {case.case_id!r} has invalid evaluation_group "
            f"{case.evaluation_group!r}"
        )
    if case.execution_adapter not in {"workflow", "retrieval", "api"}:
        raise ValueError(
            f"Case {case.case_id!r} has invalid execution_adapter "
            f"{case.execution_adapter!r}"
        )
    expected_adapter = {
        "core": "workflow",
        "robustness": "workflow",
        "rag": "retrieval",
        "api_e2e": "api",
    }[case.evaluation_group]
    if case.execution_adapter != expected_adapter:
        raise ValueError(
            f"Case {case.case_id!r} group {case.evaluation_group!r} must use "
            f"{expected_adapter!r} adapter"
        )
    if case.execution_adapter == "retrieval":
        if not case.retrieval_query.strip():
            raise ValueError(f"Retrieval case {case.case_id!r} needs retrieval_query")
        retrieval_keys = {
            *case.required_retrieved_item_keys,
            *case.any_retrieved_item_keys,
        }
        unknown_retrieval = retrieval_keys - fixture_keys
        if unknown_retrieval:
            raise ValueError(
                f"Case {case.case_id!r} references unknown retrieval items: "
                f"{unknown_retrieval}"
            )
        if not 1 <= case.retrieval_limit <= 20:
            raise ValueError(f"Case {case.case_id!r} has invalid retrieval_limit")
    if case.execution_adapter == "api" and not case.api_scenario.strip():
        raise ValueError(f"API case {case.case_id!r} needs api_scenario")
    if case.max_duration_ms < 0:
        raise ValueError(f"Case {case.case_id!r} has negative max_duration_ms")
    if case.min_llm_calls < 0 or case.max_llm_calls < 0:
        raise ValueError(f"Case {case.case_id!r} has negative LLM call bounds")
    if case.max_llm_calls and case.max_llm_calls < case.min_llm_calls:
        raise ValueError(f"Case {case.case_id!r} has inverted LLM call bounds")
    if not 0.0 <= case.min_judge_overall <= 100.0:
        raise ValueError(f"Case {case.case_id!r} has invalid judge threshold")
    for turn in case.pre_turns:
        if not turn.request.strip():
            raise ValueError(f"Case {case.case_id!r} contains an empty pre-turn")
        if turn.max_results < 1 or turn.max_results > 10:
            raise ValueError(f"Case {case.case_id!r} has invalid pre-turn max_results")
    if case.expected_status == "completed" and case.expected_route == "outfit_recommend":
        if not case.required_slots and not case.required_slot_alternatives:
            raise ValueError(
                f"Completed recommendation {case.case_id!r} needs required slots"
            )
    if case.expected_status == "infeasible" and not case.required_empty_recommendations:
        raise ValueError(f"Infeasible case {case.case_id!r} must require empty recommendations")


def load_quality_cases(path: Path = DEFAULT_CASES_PATH) -> tuple[QualityBenchmarkCase, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
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
            required_slot_alternatives=tuple(
                tuple(str(slot) for slot in alternative)
                for alternative in raw.get("required_slot_alternatives") or ()
            ),
            required_features=_strings(raw, "required_features"),
            forbidden_claims=_strings(raw, "forbidden_claims"),
            expected_missing_elements=_strings(raw, "expected_missing_elements"),
            expected_missing_slots=_strings(raw, "expected_missing_slots"),
            forbidden_missing_slots=_strings(raw, "forbidden_missing_slots"),
            expected_zero_slots=_strings(raw, "expected_zero_slots"),
            min_gap_count=int(raw.get("min_gap_count") or 0),
            required_empty_recommendations=bool(raw.get("required_empty_recommendations")),
            required_result_fields=_strings(raw, "required_result_fields"),
            min_outfit_count=int(raw.get("min_outfit_count") or 0),
            max_results=int(raw.get("max_results") or 3),
            min_scores={str(key): int(value) for key, value in (raw.get("min_scores") or {}).items()},
            split=str(raw.get("split") or raw.get("suite") or "validation"),
            evaluation_group=str(raw.get("evaluation_group") or "core"),
            execution_adapter=str(raw.get("execution_adapter") or "workflow"),
            pre_turns=tuple(
                BenchmarkTurn(
                    request=str(turn["request"]),
                    expected_route=str(turn.get("expected_route") or ""),
                    expected_status=str(turn.get("expected_status") or "completed"),
                    requested_task_type=str(turn.get("requested_task_type") or ""),
                    max_results=int(turn.get("max_results") or 1),
                )
                for turn in raw.get("pre_turns") or ()
            ),
            preseed_preferences=tuple(
                dict(preference) for preference in raw.get("preseed_preferences") or ()
            ),
            require_wardrobe_unchanged=bool(raw.get("require_wardrobe_unchanged")),
            require_preferences_unchanged=bool(raw.get("require_preferences_unchanged")),
            require_security_signal=bool(raw.get("require_security_signal")),
            expected_security_categories=_strings(raw, "expected_security_categories"),
            forbidden_output_fragments=_strings(raw, "forbidden_output_fragments"),
            required_output_fragments=_strings(raw, "required_output_fragments"),
            preserve_latest_except_slot=str(raw.get("preserve_latest_except_slot") or ""),
            max_duration_ms=int(raw.get("max_duration_ms") or 0),
            min_llm_calls=int(raw.get("min_llm_calls") or 0),
            max_llm_calls=int(raw.get("max_llm_calls") or 0),
            min_judge_overall=float(
                raw.get("min_judge_overall")
                or (
                    60.0
                    if payload.get("schema_version")
                    == "styleforge.wardrobe-quality-benchmark.v2"
                    and str(raw.get("expected_status") or "") == "completed"
                    and str(raw.get("execution_adapter") or "workflow")
                    == "workflow"
                    else 0.0
                )
            ),
            retrieval_query=str(raw.get("retrieval_query") or ""),
            retrieval_limit=int(raw.get("retrieval_limit") or 5),
            required_retrieved_item_keys=_strings(
                raw, "required_retrieved_item_keys"
            ),
            any_retrieved_item_keys=_strings(raw, "any_retrieved_item_keys"),
            expected_retrieval_modes=_strings(raw, "expected_retrieval_modes"),
            require_semantic_retrieval=bool(raw.get("require_semantic_retrieval")),
            force_semantic_unavailable=bool(raw.get("force_semantic_unavailable")),
            api_scenario=str(raw.get("api_scenario") or ""),
        )
        _validate_case(case)
        cases.append(case)
    return tuple(cases)
