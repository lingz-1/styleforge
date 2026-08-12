"""Evaluate LLM-based preference-evidence extraction on fixed cases.

Runs the real LLM (``services.memory_extractor``) over the curated
``memory_extraction.json`` cases and reports evidence-level Precision / Recall /
F1, polarity agreement, and scope agreement.

The metric is a pure function (``match_case``), so it is unit-testable without a
provider; the CLI entry point only needs a configured DeepSeek key in ``.env``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
APPS_API_ROOT = WORKSPACE_ROOT / "apps" / "api"
if str(APPS_API_ROOT) not in sys.path:
    sys.path.insert(0, str(APPS_API_ROOT))

from styleforge.common.console import configure_utf8_console  # noqa: E402
from styleforge.common.files import write_json_atomic  # noqa: E402
from styleforge.core.config import Settings  # noqa: E402
from styleforge.llm.client import llm_client_from_settings  # noqa: E402
from styleforge.llm.memory_prompts import MEMORY_PROMPT_VERSION  # noqa: E402
from styleforge.services.memory_extractor import extract_language_evidence  # noqa: E402


DEFAULT_CASES = Path(__file__).resolve().parents[1] / "cases" / "memory_extraction.json"
DEFAULT_REPORT = WORKSPACE_ROOT / "artifacts" / "evaluation" / "memory_extraction.json"

_SCHEMA_VERSION = "styleforge.memory-extraction-eval.v1"
_PROMPT_VERSION = MEMORY_PROMPT_VERSION

_REQUIRED_FIELDS = ("dimension", "attribute", "value", "polarity")
_VALID_DIMENSIONS = {"style", "garment", "appearance", "shopping"}
_VALID_POLARITIES = {"positive", "negative"}

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", (text or "").strip()).lower()


def load_cases(path: Path) -> list[dict[str, Any]]:
    """Validate the case file: unique ids, non-empty requests, well-formed expected evidence."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("memory extraction cases must be a non-empty JSON array")

    seen_ids: set[str] = set()
    cases: list[dict[str, Any]] = []
    for index, raw_case in enumerate(payload, start=1):
        if not isinstance(raw_case, dict):
            raise ValueError(f"case {index} must be a JSON object")
        case_id = str(raw_case.get("id", "")).strip()
        request = str(raw_case.get("request", "")).strip()
        expected = raw_case.get("expected_evidence", [])
        if not case_id or case_id in seen_ids:
            raise ValueError(f"case {index} has an empty or duplicate id: {case_id!r}")
        if not request:
            raise ValueError(f"case {case_id} has an empty request")
        if not isinstance(expected, list):
            raise ValueError(f"case {case_id} expected_evidence must be a list")
        for item in expected:
            if not isinstance(item, dict):
                raise ValueError(f"case {case_id} has a non-object expected evidence item")
            missing = [field for field in _REQUIRED_FIELDS if not str(item.get(field, "")).strip()]
            if missing:
                raise ValueError(f"case {case_id} evidence missing fields: {missing}")
            if str(item.get("dimension")).strip().lower() not in _VALID_DIMENSIONS:
                raise ValueError(f"case {case_id} has an invalid dimension: {item.get('dimension')!r}")
            if str(item.get("polarity")).strip().lower() not in _VALID_POLARITIES:
                raise ValueError(f"case {case_id} has an invalid polarity: {item.get('polarity')!r}")
        seen_ids.add(case_id)
        cases.append({**raw_case, "id": case_id, "request": request})
    return cases


def evidence_key(evidence: dict[str, Any]) -> tuple[str, str, str]:
    """Normalized (dimension, attribute, value) triple used for matching."""
    return (
        _normalize(evidence.get("dimension", "")),
        _normalize(evidence.get("attribute", "")),
        _normalize(evidence.get("value", "")),
    )


def scope_matches(actual: dict[str, Any] | None, expected: dict[str, Any] | None) -> bool:
    """Whether two scopes agree: type must match, contextual occasions must overlap."""
    actual_scope = actual or {}
    expected_scope = expected or {}
    if actual_scope.get("type") != expected_scope.get("type"):
        return False
    if expected_scope.get("type") == "contextual":
        actual_occasions = {
            _normalize(occ) for occ in actual_scope.get("occasions") or []
        }
        expected_occasions = {
            _normalize(occ) for occ in expected_scope.get("occasions") or []
        }
        # Expected occasions are the ground truth; every one must be present.
        return bool(expected_occasions and expected_occasions <= actual_occasions)
    return True


def match_case(actual: list[dict[str, Any]], expected: list[dict[str, Any]]) -> dict[str, Any]:
    """Evidence-level matching for one case.

    Returns tp / fp / fn plus, among the matched rows, how many agree on
    polarity and on scope.
    """
    actual_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in actual:
        actual_by_key.setdefault(evidence_key(item), item)

    tp = 0
    polarity_ok = 0
    scope_ok = 0
    for item in expected:
        key = evidence_key(item)
        hit = actual_by_key.get(key)
        if hit is None:
            continue
        tp += 1
        if _normalize(hit.get("polarity", "")) == _normalize(item.get("polarity", "")):
            polarity_ok += 1
        if scope_matches(hit.get("scope"), item.get("scope")):
            scope_ok += 1
    fn = len(expected) - tp
    fp = len(actual) - tp
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "polarity_agreement": round(polarity_ok / tp, 4) if tp else 0.0,
        "scope_agreement": round(scope_ok / tp, 4) if tp else 0.0,
    }


def evaluate_memory_extraction(
    cases_path: Path = DEFAULT_CASES,
    *,
    llm_client: Any,
) -> dict[str, Any]:
    """Run the real extractor over every case and aggregate the metrics."""
    cases = load_cases(cases_path)
    per_case: list[dict[str, Any]] = []
    totals = Counter()
    total_tp = total_fp = total_fn = 0
    polarity_ok = scope_ok = matched = 0
    empty_case_false_positives = 0

    for case in cases:
        actual = extract_language_evidence(llm_client, case["request"])
        expected = case["expected_evidence"]
        metrics = match_case(actual, expected)
        total_tp += metrics["tp"]
        total_fp += metrics["fp"]
        total_fn += metrics["fn"]
        polarity_ok += metrics["polarity_agreement"] * metrics["tp"]
        scope_ok += metrics["scope_agreement"] * metrics["tp"]
        matched += metrics["tp"]
        if not expected and actual:
            empty_case_false_positives += 1
        per_case.append(
            {
                "id": case["id"],
                "rule": case.get("rule", ""),
                "request": case["request"],
                "actual_evidence": [
                    {
                        "dimension": e["dimension"],
                        "attribute": e["attribute"],
                        "value": e["value"],
                        "polarity": e["polarity"],
                        "scope": e.get("scope"),
                    }
                    for e in actual
                ],
                "expected_evidence": expected,
                "metrics": metrics,
            }
        )
        totals[case.get("rule", "unknown")] += 1

    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) else 0.0
    recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "schema_version": _SCHEMA_VERSION,
        "prompt_version": _PROMPT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cases_path": str(cases_path.resolve()),
        "case_count": len(cases),
        "metrics": {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "polarity_agreement": round(polarity_ok / matched, 4) if matched else 0.0,
            "scope_agreement": round(scope_ok / matched, 4) if matched else 0.0,
            "empty_case_false_positives": empty_case_false_positives,
        },
        "rule_distribution": dict(sorted(totals.items())),
        "per_case": per_case,
        "limitations": [
            "Curated Chinese cases; does not yet sample production traffic.",
            "Value matching is exact after normalization; paraphrases are scored as misses.",
            "Polarity/scope agreements are only counted over matched (tp) rows.",
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
    settings = Settings.from_env()
    llm_client = llm_client_from_settings(settings)
    if llm_client is None:
        print("DEEPSEEK_API_KEY is not set; memory-extraction eval requires a real LLM.", file=sys.stderr)
        raise SystemExit(2)
    report = evaluate_memory_extraction(args.cases, llm_client=llm_client)
    write_json_atomic(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
