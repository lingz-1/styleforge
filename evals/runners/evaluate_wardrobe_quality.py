"""Run the fixed-wardrobe recommendation-quality benchmark.

The default deterministic mode never calls an LLM. It evaluates routing,
status, hard outfit constraints, feature coverage, forbidden claims and the
five-dimension Critic scores when present. Configured real-LLM execution is a
separate explicit mode so routine regression runs cannot spend API quota.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
APPS_API_ROOT = WORKSPACE_ROOT / "apps" / "api"
if str(APPS_API_ROOT) not in sys.path:
    sys.path.insert(0, str(APPS_API_ROOT))

from evals.quality_benchmark import (  # noqa: E402
    DEFAULT_CASES_PATH,
    QualityBenchmarkCase,
    load_quality_cases,
)
from evals.wardrobe_fixtures import (  # noqa: E402
    WardrobeFixture,
    fixture_item_id,
    load_fixture,
    seed_fixture,
)
from styleforge.common.console import configure_utf8_console  # noqa: E402
from styleforge.common.files import write_json_atomic  # noqa: E402
from styleforge.common.observability import observability  # noqa: E402
from styleforge.core.categories import infer_slot  # noqa: E402
from styleforge.core.config import Settings  # noqa: E402
from styleforge.integrations.embeddings.text_embedder import TextEmbedder  # noqa: E402
from styleforge.llm.client import llm_client_from_settings  # noqa: E402
from styleforge.llm.system_judge import judge_system_result  # noqa: E402
from styleforge.models.task import TaskExecutionInput  # noqa: E402
from styleforge.models.task import CandidateItem  # noqa: E402
from styleforge.agentic.context.prompt_security import (  # noqa: E402
    scan_prompt_injection,
)
from styleforge.agentic.intent_constraints import item_feature_evidence  # noqa: E402
from styleforge.orchestration.task_router import TaskType  # noqa: E402
from styleforge.repositories.database import (  # noqa: E402
    connect,
    database_session,
)
from styleforge.repositories.wardrobe_repository import list_items  # noqa: E402
from styleforge.repositories.preference_model_repository import (  # noqa: E402
    list_preferences,
)
from styleforge.repositories.personal_embedding_repository import (  # noqa: E402
    upsert_personal_embedding,
)
from styleforge.services.chat_service import outfit_context_from_payload  # noqa: E402
from styleforge.services.memory_aggregator import apply_evidence  # noqa: E402
from styleforge.services.wardrobe_retrieval import (  # noqa: E402
    WardrobeHybridRetriever,
)
from styleforge.tools.weather import OpenMeteoProvider  # noqa: E402
from styleforge.tools.web_search import TavilySearchProvider  # noqa: E402
from styleforge.workflow.task_workflow import MultiTaskWorkflow  # noqa: E402


DEFAULT_REPORT = WORKSPACE_ROOT / "artifacts" / "evaluation" / "wardrobe_quality.json"
Executor = Callable[[QualityBenchmarkCase, WardrobeFixture], dict[str, Any]]
ProgressCallback = Callable[[int, int, dict[str, Any]], None]


def _item_fact_text(item: Any) -> str:
    """Render the same conservative item evidence used by runtime gates."""
    features = sorted(item_feature_evidence(item))
    return (
        f"{item.item_id} | {item.item_type} | {item.name} | "
        f"{item.color} | features={features} | {item.description}"
    )


def _recommendations(result: dict[str, Any]) -> list[dict[str, Any]]:
    structured = result.get("structured_result")
    if isinstance(structured, dict) and isinstance(structured.get("recommendations"), list):
        return structured["recommendations"]
    recommendations = result.get("recommendations")
    return recommendations if isinstance(recommendations, list) else []


def _outfits(task_type: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    if task_type == "outfit_recommend":
        return _recommendations(result)
    if task_type == "item_advice":
        outfits = result.get("sample_outfits")
        return outfits if isinstance(outfits, list) else []
    if task_type == "outfit_modify":
        outfits = result.get("alternatives")
        return outfits if isinstance(outfits, list) else []
    if task_type == "wardrobe_compatibility":
        outfits = result.get("sample_outfits")
        return outfits if isinstance(outfits, list) else []
    return []


def _item_ids(outfit: dict[str, Any]) -> tuple[str, ...]:
    raw = outfit.get("item_ids")
    if not isinstance(raw, list):
        raw = outfit.get("wardrobe_item_ids")
    return tuple(str(value) for value in raw or ())


def _gap_text(result: dict[str, Any]) -> str:
    return json.dumps(result.get("gaps") or [], ensure_ascii=False).lower()


def evaluate_case(
    case: QualityBenchmarkCase,
    payload: dict[str, Any],
    fixture: WardrobeFixture,
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    actual_route = str(payload.get("task_type") or "")
    actual_status = str(payload.get("status") or "")
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}

    def check(condition: bool, code: str, detail: str) -> None:
        if not condition:
            issues.append({"code": code, "detail": detail})

    check(
        actual_route == case.expected_route,
        "route_mismatch",
        f"expected {case.expected_route}, got {actual_route or '<empty>'}",
    )
    check(
        actual_status == case.expected_status,
        "status_mismatch",
        f"expected {case.expected_status}, got {actual_status or '<empty>'}",
    )

    duration_ms = float(payload.get("_duration_ms") or 0.0)
    if case.max_duration_ms:
        check(
            duration_ms <= case.max_duration_ms,
            "duration_limit_exceeded",
            f"expected <= {case.max_duration_ms}ms, got {duration_ms:.2f}ms",
        )
    total_llm_calls = int(
        payload.get("_total_llm_call_count")
        if payload.get("_total_llm_call_count") is not None
        else payload.get("llm_call_count") or 0
    )
    if case.min_llm_calls:
        check(
            total_llm_calls >= case.min_llm_calls,
            "llm_calls_below_minimum",
            f"expected at least {case.min_llm_calls} LLM calls, got {total_llm_calls}",
        )
    if case.max_llm_calls:
        check(
            total_llm_calls <= case.max_llm_calls,
            "llm_calls_above_maximum",
            f"expected at most {case.max_llm_calls} LLM calls, got {total_llm_calls}",
        )
    independent_judge = (
        payload.get("_independent_judge")
        if isinstance(payload.get("_independent_judge"), dict)
        else {}
    )
    if case.min_judge_overall:
        judge_overall = independent_judge.get("overall")
        check(
            isinstance(judge_overall, (int, float)),
            "independent_judge_missing",
            str(payload.get("_independent_judge_error") or "judge result is missing"),
        )
        if isinstance(judge_overall, (int, float)):
            check(
                float(judge_overall) >= case.min_judge_overall,
                "independent_judge_below_minimum",
                f"expected >= {case.min_judge_overall}, got {judge_overall}",
            )
    pre_turn_results = payload.get("_pre_turn_results") or []
    for index, pre_turn in enumerate(case.pre_turns):
        actual = pre_turn_results[index] if index < len(pre_turn_results) else {}
        if pre_turn.expected_route:
            check(
                actual.get("task_type") == pre_turn.expected_route,
                "pre_turn_route_mismatch",
                f"turn {index + 1}: expected {pre_turn.expected_route}, "
                f"got {actual.get('task_type') or '<empty>'}",
            )
        check(
            actual.get("status") == pre_turn.expected_status,
            "pre_turn_status_mismatch",
            f"turn {index + 1}: expected {pre_turn.expected_status}, "
            f"got {actual.get('status') or '<empty>'}",
        )
    before_items = set(payload.get("_wardrobe_before") or [])
    after_items = set(payload.get("_wardrobe_after") or [])
    if case.require_wardrobe_unchanged:
        check(
            before_items == after_items,
            "wardrobe_mutated",
            f"added={sorted(after_items - before_items)}, removed={sorted(before_items - after_items)}",
        )
    preferences_before = payload.get("_preferences_before") or []
    preferences_after = payload.get("_preferences_after") or []
    if case.require_preferences_unchanged:
        check(
            preferences_before == preferences_after,
            "preferences_mutated",
            "turn-scoped request changed the long-term preference model",
        )
    detected_categories = set(payload.get("_security_categories") or [])
    expected_security = set(case.expected_security_categories)
    check(
        expected_security <= detected_categories,
        "security_category_missing",
        f"missing security categories: {sorted(expected_security - detected_categories)}",
    )
    if case.require_security_signal:
        check(
            bool(payload.get("_security_signal_detected")),
            "security_signal_missing",
            "runtime prompt-security instrumentation did not observe the injected data",
        )

    if case.execution_adapter == "retrieval":
        retrieved_ids = {
            str(value) for value in result.get("retrieved_item_ids") or []
        }
        required_retrieved = {
            fixture_item_id(key) for key in case.required_retrieved_item_keys
        }
        any_retrieved = {
            fixture_item_id(key) for key in case.any_retrieved_item_keys
        }
        check(
            required_retrieved <= retrieved_ids,
            "required_retrieval_missing",
            f"missing retrieved ids: {sorted(required_retrieved - retrieved_ids)}",
        )
        if any_retrieved:
            check(
                bool(any_retrieved & retrieved_ids),
                "any_retrieval_missing",
                f"expected one of: {sorted(any_retrieved)}",
            )
        fixture_ids = {item.item_id for item in fixture.items}
        check(
            retrieved_ids <= fixture_ids,
            "retrieval_allowlist_violation",
            f"foreign retrieved ids: {sorted(retrieved_ids - fixture_ids)}",
        )
        retrieval_mode = str(result.get("mode") or "")
        if case.expected_retrieval_modes:
            check(
                retrieval_mode in set(case.expected_retrieval_modes),
                "retrieval_mode_mismatch",
                f"expected {case.expected_retrieval_modes}, got {retrieval_mode!r}",
            )
        if case.require_semantic_retrieval:
            check(
                bool(result.get("semantic_available")),
                "semantic_retrieval_unavailable",
                "semantic retrieval was required but unavailable",
            )

    api_checks = payload.get("_api_checks")
    if case.execution_adapter == "api":
        check(
            isinstance(api_checks, dict) and bool(api_checks),
            "api_checks_missing",
            "API adapter did not return endpoint assertions",
        )
        if isinstance(api_checks, dict):
            failed_api_checks = sorted(
                name for name, passed in api_checks.items() if not bool(passed)
            )
            check(
                not failed_api_checks,
                "api_contract_failed",
                f"failed API checks: {failed_api_checks}",
            )

    outfits = _outfits(actual_route, result)
    if case.required_empty_recommendations:
        check(not outfits, "recommendations_not_empty", "expected no outfit result")
    check(
        len(outfits) >= case.min_outfit_count,
        "outfit_count_below_minimum",
        f"expected at least {case.min_outfit_count} outfits, got {len(outfits)}",
    )
    missing_result_fields = [
        field for field in case.required_result_fields if field not in result
    ]
    check(
        not missing_result_fields,
        "result_field_missing",
        f"missing result fields: {missing_result_fields}",
    )

    top = outfits[0] if outfits else {}
    top_ids = set(_item_ids(top))
    by_id = {item.item_id: item for item in fixture.items}
    top_items = [by_id[item_id] for item_id in top_ids if item_id in by_id]
    unknown_ids = top_ids - set(by_id)
    check(not unknown_ids, "foreign_item", f"items outside fixture wardrobe: {sorted(unknown_ids)}")

    if case.preserve_latest_except_slot:
        previous_ids: set[str] = set()
        for previous in reversed(pre_turn_results):
            previous_result = (
                previous.get("result") if isinstance(previous.get("result"), dict) else {}
            )
            previous_outfits = _outfits(str(previous.get("task_type") or ""), previous_result)
            if previous_outfits:
                previous_ids = set(_item_ids(previous_outfits[0]))
                break
        preserved = {
            item_id
            for item_id in previous_ids
            if item_id in by_id
            and infer_slot(by_id[item_id].item_type) != case.preserve_latest_except_slot
        }
        check(
            bool(previous_ids),
            "latest_outfit_missing",
            "no prior outfit was available for multi-turn preservation",
        )
        check(
            preserved <= top_ids,
            "latest_outfit_not_preserved",
            f"lost non-target items: {sorted(preserved - top_ids)}",
        )
        previous_target = {
            item_id
            for item_id in previous_ids
            if item_id in by_id
            and infer_slot(by_id[item_id].item_type) == case.preserve_latest_except_slot
        }
        current_target = {
            item_id
            for item_id in top_ids
            if item_id in by_id
            and infer_slot(by_id[item_id].item_type) == case.preserve_latest_except_slot
        }
        check(
            bool(current_target) and current_target != previous_target,
            "target_slot_not_changed",
            f"slot {case.preserve_latest_except_slot} did not change",
        )

    required_ids = {fixture_item_id(key) for key in case.required_item_keys}
    forbidden_ids = {fixture_item_id(key) for key in case.forbidden_item_keys}
    check(
        required_ids <= top_ids,
        "required_item_missing",
        f"missing item ids: {sorted(required_ids - top_ids)}",
    )
    check(
        not (forbidden_ids & top_ids),
        "forbidden_item_present",
        f"forbidden item ids: {sorted(forbidden_ids & top_ids)}",
    )

    actual_slots = {infer_slot(item.item_type) for item in top_items}
    required_slots = set(case.required_slots)
    check(
        required_slots <= actual_slots,
        "required_slot_missing",
        f"missing slots: {sorted(required_slots - actual_slots)}",
    )
    if case.required_slot_alternatives:
        check(
            any(
                set(alternative) <= actual_slots
                for alternative in case.required_slot_alternatives
            ),
            "required_slot_alternative_missing",
            "expected one complete slot pattern: "
            f"{case.required_slot_alternatives}, got {sorted(actual_slots)}",
        )
    actual_features = {feature for item in top_items for feature in item.features}
    required_features = set(case.required_features)
    check(
        required_features <= actual_features,
        "required_feature_missing",
        f"missing features: {sorted(required_features - actual_features)}",
    )

    result_text = json.dumps(result, ensure_ascii=False).lower()
    false_claims = [claim for claim in case.forbidden_claims if claim.lower() in result_text]
    check(not false_claims, "forbidden_claim", f"unsupported claims: {false_claims}")
    forbidden_fragments = [
        fragment
        for fragment in case.forbidden_output_fragments
        if fragment.lower() in result_text
    ]
    check(
        not forbidden_fragments,
        "forbidden_output_fragment",
        f"forbidden output fragments: {forbidden_fragments}",
    )
    missing_fragments = [
        fragment
        for fragment in case.required_output_fragments
        if fragment.lower() not in result_text
    ]
    check(
        not missing_fragments,
        "required_output_fragment_missing",
        f"missing output fragments: {missing_fragments}",
    )

    scores = top.get("dimension_scores") if isinstance(top, dict) else None
    if case.min_scores:
        check(isinstance(scores, dict), "dimension_scores_missing", "top outfit has no scores")
        if isinstance(scores, dict):
            below = {
                key: {"minimum": minimum, "actual": scores.get(key)}
                for key, minimum in case.min_scores.items()
                if not isinstance(scores.get(key), (int, float)) or scores[key] < minimum
            }
            check(not below, "score_below_minimum", json.dumps(below, ensure_ascii=False))

    gap_text = _gap_text(result)
    missing_elements = [
        value for value in case.expected_missing_elements if value.lower() not in gap_text
    ]
    missing_slots = [
        value for value in case.expected_missing_slots if value.lower() not in gap_text
    ]
    check(not missing_elements, "expected_gap_missing", f"missing elements: {missing_elements}")
    check(not missing_slots, "expected_gap_slot_missing", f"missing slots: {missing_slots}")
    forbidden_gap_slots = [
        value for value in case.forbidden_missing_slots if value.lower() in gap_text
    ]
    check(
        not forbidden_gap_slots,
        "false_gap_slot",
        f"slots incorrectly reported missing: {forbidden_gap_slots}",
    )
    slot_counts = result.get("slot_counts") if isinstance(result.get("slot_counts"), dict) else {}
    nonzero_slots = [
        slot for slot in case.expected_zero_slots if int(slot_counts.get(slot) or 0) != 0
    ]
    check(
        not nonzero_slots,
        "expected_zero_slot_nonzero",
        f"expected zero-count slots: {nonzero_slots}",
    )
    gap_count = result.get("gap_count")
    check(
        case.min_gap_count == 0
        or (isinstance(gap_count, int) and gap_count >= case.min_gap_count),
        "gap_count_below_minimum",
        f"expected at least {case.min_gap_count} gaps, got {gap_count}",
    )

    categories = sorted({issue["code"] for issue in issues})
    agentic_outcome = (
        payload.get("agentic_outcome")
        if isinstance(payload.get("agentic_outcome"), dict)
        else {}
    )
    handoff = agentic_outcome.get("handoff_result")
    handoff_status = str(getattr(handoff, "status", "") or "")
    handoff_trace = getattr(handoff, "trace_summary", None)
    working_draft = agentic_outcome.get("working_draft")
    working_outfit = getattr(working_draft, "outfit", None)
    return {
        "id": case.case_id,
        "run_id": str(payload.get("run_id") or ""),
        "fixture": case.fixture,
        "request": case.request,
        "expected_route": case.expected_route,
        "actual_route": actual_route,
        "expected_status": case.expected_status,
        "actual_status": actual_status,
        "passed": not issues,
        "generation_mode": str(
            result.get("generation_mode")
            or ("llm" if payload.get("llm_enabled") else "deterministic")
        ),
        "suite": case.split,
        "split": case.split,
        "evaluation_group": case.evaluation_group,
        "execution_adapter": case.execution_adapter,
        "duration_ms": round(duration_ms, 2),
        "llm_call_count": int(payload.get("llm_call_count") or 0),
        "total_llm_call_count": total_llm_calls,
        "pre_turn_count": len(case.pre_turns),
        "security_signal_detected": bool(payload.get("_security_signal_detected")),
        "security_categories": sorted(detected_categories),
        "wardrobe_unchanged": before_items == after_items,
        "preferences_unchanged": preferences_before == preferences_after,
        "independent_judge": independent_judge,
        "judge_duration_ms": round(float(payload.get("_judge_duration_ms") or 0.0), 2),
        "message": str(result.get("message") or result.get("summary") or ""),
        "clarification_question": str(result.get("clarification_question") or ""),
        "top_item_ids": sorted(top_ids),
        "top_slots": sorted(actual_slots),
        "top_features": sorted(actual_features),
        "dimension_scores": scores if isinstance(scores, dict) else {},
        "issue_categories": categories,
        "issues": issues,
        "runtime_debug": {
            "execution_error": str(payload.get("_execution_error") or ""),
            "execution_error_message": str(
                payload.get("_execution_error_message") or ""
            ),
            "agentic_status": str(agentic_outcome.get("status") or ""),
            "agentic_clarification_question": str(
                agentic_outcome.get("clarification_question") or ""
            ),
            "extension_validation_failures": list(
                agentic_outcome.get("extension_validation_failures") or []
            ),
            "candidate_count": len(agentic_outcome.get("candidates") or []),
            "outcome_task_type": str(agentic_outcome.get("task_type") or ""),
            "handoff_status": handoff_status,
            "handoff_trace": handoff_trace or {},
            "working_draft_item_count": len(
                getattr(working_outfit, "item_ids", None) or []
            ),
            "candidate_recovered": bool(
                agentic_outcome.get("candidate_recovered")
            ),
            "candidate_recovery_attempted": bool(
                agentic_outcome.get("candidate_recovery_attempted")
            ),
            "candidate_recovery_issues": list(
                agentic_outcome.get("candidate_recovery_issues") or []
            ),
            "diagnostics": (
                payload.get("diagnostics")
                if isinstance(payload.get("diagnostics"), dict)
                else {}
            ),
        },
        "actual_result": result,
    }


def evaluate_benchmark(
    executor: Executor,
    *,
    cases_path: Path = DEFAULT_CASES_PATH,
    selected_ids: set[str] | None = None,
    selected_suites: set[str] | None = None,
    selected_groups: set[str] | None = None,
    execution_mode: str | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    cases = load_quality_cases(cases_path)
    if execution_mode:
        cases = tuple(case for case in cases if execution_mode in case.execution_modes)
    if selected_ids:
        cases = tuple(case for case in cases if case.case_id in selected_ids)
        missing = selected_ids - {case.case_id for case in cases}
        if missing:
            raise KeyError(f"Unknown benchmark case ids: {sorted(missing)}")
    if selected_suites:
        cases = tuple(case for case in cases if case.split in selected_suites)
    if selected_groups:
        cases = tuple(
            case for case in cases if case.evaluation_group in selected_groups
        )
    results: list[dict[str, Any]] = []
    for case in cases:
        fixture = load_fixture(case.fixture)
        try:
            payload = executor(case, fixture)
        except Exception as error:
            payload = {
                "task_type": "",
                "status": "execution_error",
                "result": {},
            }
            result = evaluate_case(case, payload, fixture)
            result["execution_error"] = type(error).__name__
            result["execution_error_message"] = str(error)
        else:
            result = evaluate_case(case, payload, fixture)
            if payload.get("_execution_error"):
                result["execution_error"] = payload["_execution_error"]
                result["execution_error_message"] = payload.get(
                    "_execution_error_message", ""
                )
        results.append(result)
        if progress_callback is not None:
            progress_callback(len(results), len(cases), result)

    issue_counts = Counter(
        category for result in results for category in result["issue_categories"]
    )
    passed = sum(bool(result["passed"]) for result in results)
    route_passed = sum(
        result["actual_route"] == result["expected_route"] for result in results
    )
    status_passed = sum(
        result["actual_status"] == result["expected_status"] for result in results
    )
    count = len(results)
    durations = sorted(float(result.get("duration_ms") or 0.0) for result in results)
    judge_overalls = [
        float(result["independent_judge"]["overall"])
        for result in results
        if isinstance(result.get("independent_judge"), dict)
        and isinstance(result["independent_judge"].get("overall"), (int, float))
    ]

    def percentile(values: list[float], percentile_value: float) -> float:
        if not values:
            return 0.0
        index = max(0, min(len(values) - 1, int((len(values) * percentile_value) + 0.999999) - 1))
        return round(values[index], 2)

    grouped: dict[str, dict[str, list[bool]]] = {
        "route": defaultdict(list),
        "fixture": defaultdict(list),
        "split": defaultdict(list),
        "evaluation_group": defaultdict(list),
    }
    for result in results:
        grouped["route"][result["expected_route"]].append(bool(result["passed"]))
        grouped["fixture"][result["fixture"]].append(bool(result["passed"]))
        grouped["split"][result["split"]].append(bool(result["passed"]))
        grouped["evaluation_group"][result["evaluation_group"]].append(
            bool(result["passed"])
        )

    def group_metrics(values: dict[str, list[bool]]) -> dict[str, Any]:
        return {
            key: {
                "case_count": len(flags),
                "passed_count": sum(flags),
                "pass_rate": round(sum(flags) / len(flags), 6),
            }
            for key, flags in sorted(values.items())
        }

    return {
        "schema_version": "styleforge.wardrobe-quality-eval.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cases_path": str(cases_path.resolve()),
        "case_count": count,
        "passed_count": passed,
        "metrics": {
            "pass_rate": round(passed / count, 6) if count else 0.0,
            "route_accuracy": round(route_passed / count, 6) if count else 0.0,
            "status_accuracy": round(status_passed / count, 6) if count else 0.0,
            "duration_ms": {
                "average": round(sum(durations) / len(durations), 2) if durations else 0.0,
                "p95": percentile(durations, 0.95),
                "maximum": round(max(durations), 2) if durations else 0.0,
            },
            "independent_judge": {
                "evaluated_count": len(judge_overalls),
                "mean_overall": (
                    round(sum(judge_overalls) / len(judge_overalls), 2)
                    if judge_overalls
                    else 0.0
                ),
            },
        },
        "metrics_by_route": group_metrics(grouped["route"]),
        "metrics_by_fixture": group_metrics(grouped["fixture"]),
        "metrics_by_split": group_metrics(grouped["split"]),
        "metrics_by_evaluation_group": group_metrics(grouped["evaluation_group"]),
        "metrics_by_suite": group_metrics(grouped["split"]),
        "issue_counts": dict(sorted(issue_counts.items())),
        "results": results,
    }


def _scoped_dsn(dsn: str, schema: str) -> str:
    separator = "&" if "?" in dsn else "?"
    return f"{dsn}{separator}options=-csearch_path%3D{schema}"


def _workflow_executor(
    base_dsn: str,
    *,
    configured_llm: bool,
) -> tuple[Executor, Callable[[], None]]:
    settings = Settings.from_env()
    llm = llm_client_from_settings(settings) if configured_llm else None
    if configured_llm and llm is None:
        raise RuntimeError("Configured mode requires DEEPSEEK_API_KEY")
    schema = f"quality_eval_{uuid.uuid4().hex[:12]}"

    def drop_schema() -> None:
        connection = connect(base_dsn)
        try:
            connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            connection.commit()
        finally:
            connection.close()

    admin = connect(base_dsn)
    try:
        admin.execute(f'CREATE SCHEMA "{schema}"')
        admin.commit()
    finally:
        admin.close()
    dsn = _scoped_dsn(base_dsn, schema)
    try:
        workflow = MultiTaskWorkflow(
            database_path=dsn,
            knowledge_root=settings.knowledge_root,
            llm_client=llm,
            web_search_provider=(
                TavilySearchProvider(
                    api_key=settings.tavily_api_key,
                    timeout=settings.web_search_timeout,
                    max_results=settings.web_search_max_results,
                )
                if configured_llm and settings.web_search_enabled and settings.tavily_api_key
                else None
            ),
            weather_provider=(
                OpenMeteoProvider(timeout=settings.weather_timeout)
                if configured_llm and settings.weather_enabled
                else None
            ),
            skills_root=settings.knowledge_root / "skills",
            default_location=settings.weather_default_location,
        )
    except Exception:
        drop_schema()
        raise
    retrieval_runtime = WardrobeHybridRetriever(
        embedding_dir=settings.embedding_dir,
        model_dir=settings.artifact_root / "models",
    )

    def unavailable_semantic_runtime() -> Any:
        raise RuntimeError("semantic retrieval intentionally disabled by benchmark")

    keyword_only_runtime = WardrobeHybridRetriever(
        embedding_dir=settings.embedding_dir,
        model_dir=settings.artifact_root / "models",
        embedder_factory=unavailable_semantic_runtime,
    )
    semantic_eval_runtime: WardrobeHybridRetriever | None = None
    semantic_eval_embedder: TextEmbedder | None = None
    api_client: Any | None = None

    def get_api_client() -> Any:
        nonlocal api_client
        if api_client is not None:
            return api_client
        from fastapi.testclient import TestClient

        previous_dsn = os.environ.get("STYLEFORGE_DATABASE_DSN")
        os.environ["STYLEFORGE_DATABASE_DSN"] = dsn
        try:
            from styleforge import api as api_module
        finally:
            if previous_dsn is None:
                os.environ.pop("STYLEFORGE_DATABASE_DSN", None)
            else:
                os.environ["STYLEFORGE_DATABASE_DSN"] = previous_dsn

        api_module.settings = replace(api_module.settings, database_dsn=dsn)
        api_module.get_multi_task_workflow = lambda: workflow
        api_client = TestClient(api_module.app)
        return api_client

    def execute(case: QualityBenchmarkCase, fixture: WardrobeFixture) -> dict[str, Any]:
        case_user_id = f"eval-{uuid.uuid5(uuid.NAMESPACE_URL, f'styleforge:{case.case_id}')}"
        with database_session(dsn) as connection:
            seeded = seed_fixture(connection, fixture.name, user_id=case_user_id)
            if case.preseed_preferences:
                apply_evidence(
                    connection,
                    case_user_id,
                    [dict(value) for value in case.preseed_preferences],
                )
            wardrobe_before = sorted(
                item.item_id for item in list_items(connection, case_user_id)
            )
            preferences_before = [
                {
                    key: preference.get(key)
                    for key in ("dimension", "attribute", "value", "polarity", "lifecycle")
                }
                for preference in list_preferences(connection, case_user_id, limit=1000)
            ]

        if case.execution_adapter == "retrieval":
            started_at = perf_counter()
            with database_session(dsn) as connection:
                wardrobe_items = list_items(connection, case_user_id)
                if case.require_semantic_retrieval:
                    nonlocal semantic_eval_runtime, semantic_eval_embedder
                    if semantic_eval_runtime is None:
                        try:
                            semantic_eval_embedder = TextEmbedder(
                                settings.artifact_root / "models",
                                device="cuda",
                                precision="float16",
                            )
                        except Exception:
                            semantic_eval_embedder = TextEmbedder(
                                settings.artifact_root / "models",
                                device="cpu",
                                precision="float32",
                            )
                        semantic_eval_runtime = WardrobeHybridRetriever(
                            embedding_dir=settings.embedding_dir,
                            model_dir=settings.artifact_root / "models",
                            text_embedder=semantic_eval_embedder,
                        )
                    prompts = [
                        f"a fashion item {item.item_type} {item.name} "
                        f"{item.color} {item.description}"
                        for item in wardrobe_items
                    ]
                    if semantic_eval_embedder is None:
                        raise RuntimeError("Semantic evaluation embedder is unavailable")
                    vectors = semantic_eval_embedder.embed_texts(prompts)
                    timestamp = datetime.now(timezone.utc).isoformat()
                    for item, vector in zip(wardrobe_items, vectors, strict=True):
                        connection.execute(
                            """
                            INSERT INTO personal_wardrobe_items(
                                item_id, user_id, ownership_status, review_status,
                                created_at, updated_at
                            ) VALUES (%s, %s, 'owned', 'confirmed', %s, %s)
                            ON CONFLICT(item_id) DO UPDATE SET
                                user_id = excluded.user_id,
                                ownership_status = 'owned',
                                review_status = 'confirmed',
                                updated_at = excluded.updated_at
                            """,
                            (item.item_id, case_user_id, timestamp, timestamp),
                        )
                        upsert_personal_embedding(
                            connection,
                            item_id=item.item_id,
                            vector=vector,
                            embedding_kind="text",
                            model_revision="styleforge-quality-eval-v2",
                        )
                    retriever = semantic_eval_runtime
                elif case.force_semantic_unavailable:
                    retriever = keyword_only_runtime
                else:
                    retriever = retrieval_runtime
                outcome = retriever.search(
                    case.retrieval_query,
                    wardrobe_items,
                    connection=connection,
                    limit=case.retrieval_limit,
                )
            return {
                "task_type": "wardrobe_retrieval",
                "status": "completed",
                "result": {
                    "retrieved_item_ids": outcome.item_ids,
                    "matched": outcome.matched,
                    "mode": outcome.mode,
                    "semantic_available": outcome.semantic_available,
                    "scores": outcome.final_scores,
                    "diagnostics": outcome.diagnostics,
                },
                "_duration_ms": (perf_counter() - started_at) * 1000.0,
                "_total_llm_call_count": 0,
                "_wardrobe_before": wardrobe_before,
                "_wardrobe_after": wardrobe_before,
                "_preferences_before": preferences_before,
                "_preferences_after": preferences_before,
                "_fixture_user_id": seeded.user_id,
            }

        if case.execution_adapter == "api":
            started_at = perf_counter()
            client = get_api_client()
            if case.api_scenario == "cross_user_isolation":
                created = client.post(
                    f"/users/{case_user_id}/chat-sessions",
                    json={"title": "isolation probe"},
                )
                created_body = created.json() if created.status_code == 201 else {}
                session_id = str(created_body.get("session_id") or "")
                foreign_user = f"{case_user_id}-foreign"
                denied = client.get(
                    f"/chat-sessions/{session_id}",
                    params={"user_id": foreign_user},
                )
                own = client.get(
                    f"/chat-sessions/{session_id}",
                    params={"user_id": case_user_id},
                )
                return {
                    "task_type": "api_isolation",
                    "status": "completed",
                    "result": {
                        "created_status": created.status_code,
                        "foreign_status": denied.status_code,
                        "owner_status": own.status_code,
                    },
                    "_api_checks": {
                        "session_created": created.status_code == 201,
                        "foreign_user_denied": denied.status_code == 404,
                        "owner_can_read": own.status_code == 200,
                    },
                    "_duration_ms": (perf_counter() - started_at) * 1000.0,
                    "_total_llm_call_count": 0,
                    "_wardrobe_before": wardrobe_before,
                    "_wardrobe_after": wardrobe_before,
                    "_preferences_before": preferences_before,
                    "_preferences_after": preferences_before,
                    "_fixture_user_id": seeded.user_id,
                }

            session_id = ""
            api_checks: dict[str, bool] = {}
            if case.api_scenario == "chat_session_persistence":
                created = client.post(
                    f"/users/{case_user_id}/chat-sessions",
                    json={"title": "quality evaluation"},
                )
                session_id = str(
                    (created.json() if created.status_code == 201 else {}).get(
                        "session_id"
                    )
                    or ""
                )
                api_checks["session_created"] = created.status_code == 201

            request_body: dict[str, Any] = {
                "user_id": case_user_id,
                "request": case.request,
                "max_results": case.max_results,
                "current_outfit_id": case.current_outfit_id,
                "current_item_ids": [
                    fixture_item_id(key) for key in case.current_item_keys
                ],
                "target_slot": case.target_slot,
                "session_id": session_id,
            }
            if case.anchor_item_key:
                anchor_id = fixture_item_id(case.anchor_item_key)
                request_body["item_id"] = anchor_id
                request_body["selected_item_id"] = anchor_id
            if case.candidate_item:
                request_body["candidate_item"] = case.candidate_item

            response = client.post("/tasks/execute", json=request_body)
            body = response.json() if response.status_code == 200 else {
                "task_type": "",
                "status": "http_error",
                "result": {},
                "http_error": response.json(),
            }
            api_checks["execute_status_200"] = response.status_code == 200
            api_checks["response_has_run_id"] = bool(body.get("run_id"))

            if case.api_scenario == "execute_and_read_task" and body.get("run_id"):
                read_response = client.get(
                    f"/tasks/{case_user_id}/{body['run_id']}"
                )
                api_checks["task_read_status_200"] = read_response.status_code == 200
                read_body = read_response.json() if read_response.status_code == 200 else {}
                api_checks["task_read_same_run"] = (
                    str(read_body.get("run_id") or "") == str(body.get("run_id") or "")
                )
            if case.api_scenario == "chat_session_persistence" and session_id:
                chat_response = client.get(
                    f"/chat-sessions/{session_id}",
                    params={"user_id": case_user_id},
                )
                chat_body = (
                    chat_response.json() if chat_response.status_code == 200 else {}
                )
                messages = chat_body.get("messages") or []
                api_checks["chat_read_status_200"] = chat_response.status_code == 200
                api_checks["user_and_assistant_persisted"] = (
                    {message.get("role") for message in messages}
                    >= {"user", "assistant"}
                )

            wardrobe_response = client.get(f"/wardrobes/{case_user_id}")
            wardrobe_body = (
                wardrobe_response.json() if wardrobe_response.status_code == 200 else {}
            )
            wardrobe_after = sorted(
                str(item.get("item_id"))
                for item in wardrobe_body.get("items") or []
                if item.get("item_id")
            )
            if case.api_scenario == "candidate_no_mutation":
                api_checks["candidate_not_persisted"] = (
                    set(wardrobe_after) == set(wardrobe_before)
                )
            body.update(
                {
                    "_api_checks": api_checks,
                    "_duration_ms": (perf_counter() - started_at) * 1000.0,
                    "_wardrobe_before": wardrobe_before,
                    "_wardrobe_after": wardrobe_after,
                    "_preferences_before": preferences_before,
                    "_preferences_after": preferences_before,
                    "_fixture_user_id": seeded.user_id,
                }
            )
            return body

        security_texts = [case.request]
        security_texts.extend(turn.request for turn in case.pre_turns)
        if case.candidate_item:
            security_texts.extend(
                str(case.candidate_item.get(field) or "")
                for field in ("name", "description")
            )
        security_categories = sorted(
            {
                category
                for index, value in enumerate(security_texts)
                for category in scan_prompt_injection(
                    value, source=f"benchmark_input:{index}"
                ).categories
            }
        )
        security_before = int(
            observability.snapshot()
            .get("operations", {})
            .get("prompt_security", {})
            .get("total", 0)
        )
        started_at = perf_counter()
        pre_turn_results: list[dict[str, Any]] = []
        session_context: dict[str, Any] | None = None
        total_llm_calls = 0

        for turn in case.pre_turns:
            turn_input = TaskExecutionInput(
                user_id=case_user_id,
                request=turn.request,
                max_results=turn.max_results,
                requested_task_type=(
                    TaskType(turn.requested_task_type)
                    if turn.requested_task_type
                    else None
                ),
            )
            try:
                turn_payload = workflow.execute(
                    turn_input,
                    session_context=session_context,
                )
            except Exception as error:
                turn_payload = {
                    "task_type": "",
                    "status": "execution_error",
                    "result": {},
                    "_execution_error": type(error).__name__,
                    "_execution_error_message": str(error),
                }
            pre_turn_results.append(turn_payload)
            total_llm_calls += int(turn_payload.get("llm_call_count") or 0)
            next_context = outfit_context_from_payload(turn_payload)
            if next_context.get("current_item_ids"):
                session_context = next_context

        anchor_id = fixture_item_id(case.anchor_item_key) if case.anchor_item_key else ""
        task_input = TaskExecutionInput(
            user_id=case_user_id,
            request=case.request,
            max_results=case.max_results,
            requested_task_type=(
                TaskType(case.requested_task_type)
                if case.requested_task_type
                else None
            ),
            current_outfit_id=case.current_outfit_id,
            current_item_ids=[fixture_item_id(key) for key in case.current_item_keys],
            target_slot=case.target_slot,
            item_id=anchor_id,
            selected_item_id=anchor_id,
            candidate_item=(
                CandidateItem.model_validate(case.candidate_item)
                if case.candidate_item
                else None
            ),
        )
        before_final_item_ids = list(task_input.current_item_ids)
        if not before_final_item_ids and session_context:
            before_final_item_ids = list(session_context.get("current_item_ids") or [])
        route = workflow.router.route(
            case.request,
            current_outfit_id=case.current_outfit_id,
            has_candidate_item=case.candidate_item is not None,
            requested_task_type=case.requested_task_type or None,
        )
        try:
            payload = workflow.execute(task_input, session_context=session_context)
        except Exception as error:
            payload = {
                "task_type": route.task_type.value,
                "status": "execution_error",
                "result": {},
                "_execution_error": type(error).__name__,
                "_execution_error_message": str(error),
            }
        system_duration_ms = (perf_counter() - started_at) * 1000.0
        total_llm_calls += int(payload.get("llm_call_count") or 0)
        judge_started_at = perf_counter()
        if (
            llm is not None
            and payload.get("status") == "completed"
            and isinstance(payload.get("result"), dict)
        ):
            try:
                task_type = str(payload.get("task_type") or "")
                result_body = payload["result"]
                generated_outfits = _outfits(task_type, result_body)
                selected_ids = set(_item_ids(generated_outfits[0])) if generated_outfits else set()
                item_by_id = {item.item_id: item for item in fixture.items}

                selected_texts = [
                    _item_fact_text(item_by_id[item_id])
                    for item_id in sorted(selected_ids)
                    if item_id in item_by_id
                ]
                wardrobe_texts = [
                    _item_fact_text(item) for item in fixture.items
                ]
                evaluation_context: dict[str, Any] = {}
                if task_type == "outfit_modify":
                    replaced_ids = list(result_body.get("replaced_item_ids") or [])
                    added_ids = list(result_body.get("added_item_ids") or [])
                    locked_ids = list(result_body.get("locked_item_ids") or [])
                    evaluation_context = {
                        "before_item_ids": before_final_item_ids,
                        "after_item_ids": sorted(selected_ids),
                        "before_item_facts": [
                            _item_fact_text(item_by_id[item_id])
                            for item_id in before_final_item_ids
                            if item_id in item_by_id
                        ],
                        "after_item_facts": selected_texts,
                        "replaced_item_facts": [
                            _item_fact_text(item_by_id[item_id])
                            for item_id in replaced_ids
                            if item_id in item_by_id
                        ],
                        "added_item_facts": [
                            _item_fact_text(item_by_id[item_id])
                            for item_id in added_ids
                            if item_id in item_by_id
                        ],
                        "locked_item_facts": [
                            _item_fact_text(item_by_id[item_id])
                            for item_id in locked_ids
                            if item_id in item_by_id
                        ],
                        "item_set_changed": set(before_final_item_ids) != selected_ids,
                    }
                payload["_independent_judge"] = judge_system_result(
                    llm,
                    task_type=task_type,
                    request=case.request,
                    result=result_body,
                    selected_item_texts=selected_texts,
                    wardrobe_item_texts=wardrobe_texts,
                    evaluation_context=evaluation_context,
                )
            except Exception as error:
                payload["_independent_judge_error"] = (
                    f"{type(error).__name__}: {error}"
                )
        judge_duration_ms = (perf_counter() - judge_started_at) * 1000.0
        with database_session(dsn) as connection:
            wardrobe_after = sorted(
                item.item_id for item in list_items(connection, case_user_id)
            )
            preferences_after = [
                {
                    key: preference.get(key)
                    for key in ("dimension", "attribute", "value", "polarity", "lifecycle")
                }
                for preference in list_preferences(connection, case_user_id, limit=1000)
            ]
        security_after = int(
            observability.snapshot()
            .get("operations", {})
            .get("prompt_security", {})
            .get("total", 0)
        )
        payload.update(
            {
                "_duration_ms": system_duration_ms,
                "_judge_duration_ms": judge_duration_ms,
                "_total_llm_call_count": total_llm_calls,
                "_pre_turn_results": pre_turn_results,
                "_wardrobe_before": wardrobe_before,
                "_wardrobe_after": wardrobe_after,
                "_preferences_before": preferences_before,
                "_preferences_after": preferences_after,
                "_security_categories": security_categories,
                "_security_signal_detected": security_after > security_before,
                "_fixture_user_id": seeded.user_id,
            }
        )
        return payload

    def cleanup() -> None:
        drop_schema()

    return execute, cleanup


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--case", action="append", dest="case_ids", default=[])
    parser.add_argument(
        "--split",
        "--suite",
        action="append",
        dest="splits",
        choices=("validation", "holdout"),
        default=[],
        help="Run only the validation or holdout split; may be repeated",
    )
    parser.add_argument(
        "--group",
        action="append",
        dest="groups",
        choices=("core", "robustness", "rag", "api_e2e"),
        default=[],
        help="Run only selected evaluation groups; may be repeated",
    )
    parser.add_argument(
        "--mode",
        choices=("deterministic", "configured"),
        default="deterministic",
        help="configured explicitly enables the project DeepSeek client",
    )
    parser.add_argument(
        "--database-dsn",
        default="",
        help="Base PostgreSQL DSN; defaults to STYLEFORGE_TEST_DATABASE_DSN",
    )
    parser.add_argument(
        "--print-full-report",
        action="store_true",
        help="Print every case result; the complete report is always written to --report",
    )
    return parser


def main() -> int:
    configure_utf8_console()
    args = build_parser().parse_args()
    base_dsn = (
        args.database_dsn
        or os.getenv("STYLEFORGE_TEST_DATABASE_DSN", "").strip()
        or Settings.from_env().database_dsn
    )
    if not base_dsn:
        raise SystemExit("STYLEFORGE_TEST_DATABASE_DSN or --database-dsn is required")
    executor, cleanup = _workflow_executor(
        base_dsn,
        configured_llm=args.mode == "configured",
    )
    try:
        report = evaluate_benchmark(
            executor,
            cases_path=args.cases,
            selected_ids=set(args.case_ids),
            selected_suites=set(args.splits),
            selected_groups=set(args.groups),
            execution_mode=args.mode,
            progress_callback=lambda index, total, result: print(
                f"[{index}/{total}] {result['id']} "
                f"{'PASS' if result['passed'] else 'FAIL'} "
                f"{result['duration_ms']:.0f}ms",
                flush=True,
            ),
        )
        report["mode"] = args.mode
        write_json_atomic(args.report, report)
        console_report = report if args.print_full_report else {
            "mode": report["mode"],
            "case_count": report["case_count"],
            "passed_count": report["passed_count"],
            "metrics": report["metrics"],
            "metrics_by_route": report["metrics_by_route"],
            "metrics_by_fixture": report["metrics_by_fixture"],
            "metrics_by_split": report["metrics_by_split"],
            "metrics_by_evaluation_group": report[
                "metrics_by_evaluation_group"
            ],
            "metrics_by_suite": report["metrics_by_suite"],
            "issue_counts": report["issue_counts"],
            "failed_case_ids": [
                result["id"] for result in report["results"] if not result["passed"]
            ],
            "report": str(args.report.resolve()),
        }
        print(json.dumps(console_report, ensure_ascii=False, indent=2))
        return 0 if report["passed_count"] == report["case_count"] else 1
    finally:
        cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
