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
from datetime import datetime, timezone
from pathlib import Path
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
from styleforge.core.categories import infer_slot  # noqa: E402
from styleforge.core.config import Settings  # noqa: E402
from styleforge.llm.client import llm_client_from_settings  # noqa: E402
from styleforge.models.task import TaskExecutionInput  # noqa: E402
from styleforge.models.task import CandidateItem  # noqa: E402
from styleforge.orchestration.task_router import TaskType  # noqa: E402
from styleforge.repositories.database import (  # noqa: E402
    connect,
    database_session,
)
from styleforge.tools.weather import OpenMeteoProvider  # noqa: E402
from styleforge.tools.web_search import TavilySearchProvider  # noqa: E402
from styleforge.workflow.task_workflow import MultiTaskWorkflow  # noqa: E402


DEFAULT_REPORT = WORKSPACE_ROOT / "artifacts" / "evaluation" / "wardrobe_quality.json"
Executor = Callable[[QualityBenchmarkCase, WardrobeFixture], dict[str, Any]]


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
        "llm_call_count": int(payload.get("llm_call_count") or 0),
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
    execution_mode: str | None = None,
) -> dict[str, Any]:
    cases = load_quality_cases(cases_path)
    if execution_mode:
        cases = tuple(case for case in cases if execution_mode in case.execution_modes)
    if selected_ids:
        cases = tuple(case for case in cases if case.case_id in selected_ids)
        missing = selected_ids - {case.case_id for case in cases}
        if missing:
            raise KeyError(f"Unknown benchmark case ids: {sorted(missing)}")
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

    grouped: dict[str, dict[str, list[bool]]] = {
        "route": defaultdict(list),
        "fixture": defaultdict(list),
    }
    for result in results:
        grouped["route"][result["expected_route"]].append(bool(result["passed"]))
        grouped["fixture"][result["fixture"]].append(bool(result["passed"]))

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
        },
        "metrics_by_route": group_metrics(grouped["route"]),
        "metrics_by_fixture": group_metrics(grouped["fixture"]),
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

    def execute(case: QualityBenchmarkCase, fixture: WardrobeFixture) -> dict[str, Any]:
        with database_session(dsn) as connection:
            seed_fixture(connection, fixture.name)
        anchor_id = fixture_item_id(case.anchor_item_key) if case.anchor_item_key else ""
        task_input = TaskExecutionInput(
            user_id=fixture.user_id,
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
        route = workflow.router.route(
            case.request,
            current_outfit_id=case.current_outfit_id,
            has_candidate_item=case.candidate_item is not None,
            requested_task_type=case.requested_task_type or None,
        )
        try:
            return workflow.execute(task_input)
        except Exception as error:
            return {
                "task_type": route.task_type.value,
                "status": "execution_error",
                "result": {},
                "_execution_error": type(error).__name__,
                "_execution_error_message": str(error),
            }

    def cleanup() -> None:
        drop_schema()

    return execute, cleanup


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--case", action="append", dest="case_ids", default=[])
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
    base_dsn = args.database_dsn or os.getenv("STYLEFORGE_TEST_DATABASE_DSN", "").strip()
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
            execution_mode=args.mode,
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
