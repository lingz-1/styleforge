"""Tests for the memory-extraction evaluation harness (cases + metrics).

The metric functions are pure; a real LLM is only exercised by the runner CLI
(``evals.runners.evaluate_memory_extraction``) against a key in ``.env``. These
tests use a scripted LLM so the pipeline runs offline.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.runners.evaluate_memory_extraction import (
    load_cases,
    match_case,
    evidence_key,
    scope_matches,
)
from styleforge.services.memory_extractor import extract_language_evidence

from tests.extension_llm import ScriptedExtensionLlm


CASES_PATH = Path("evals/cases/memory_extraction.json")


def _evidence(*items) -> dict:
    return {"evidence": list(items)}


def _claim(
    dimension: str,
    attribute: str,
    value: str,
    *,
    polarity: str = "positive",
    scope: dict | None = None,
) -> dict:
    return {
        "dimension": dimension,
        "attribute": attribute,
        "value": value,
        "polarity": polarity,
        "scope": scope or {"type": "global"},
    }


def test_case_file_is_valid_and_covers_core_rules() -> None:
    cases = load_cases(CASES_PATH)
    assert len(cases) == 33
    rules = {case["rule"] for case in cases}
    assert "multi_attribute_split + one_off_scene" in rules
    assert "negation_split" in rules
    assert "global_vs_contextual" in rules
    assert "whole_sentence_negation_no_claim" in rules
    # The expanded corpus must exercise the v2.3 prompt's new coverage classes.
    for rule in (
        "habit_fit_preference",
        "conditional_rule_preference",
        "material_three_way",
        "occasion_binding_contextual",
        "english_mixed_fit",
        "one_off_scene_functional",
        "multi_occasion_style_split",
        "print_as_style",
    ):
        assert rule in rules, f"missing rule {rule!r}"


def test_case_loader_rejects_invalid_evidence(tmp_path: Path) -> None:
    bad = [
        {
            "id": "x",
            "request": "显瘦是我的命",
            "expected_evidence": [
                {"dimension": "appearance", "attribute": "detail", "value": "显瘦"}
            ],
        }
    ]
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="missing fields"):
        load_cases(path)


def test_case_loader_rejects_invalid_dimension(tmp_path: Path) -> None:
    bad = [
        {
            "id": "x",
            "request": "帮我搭一套",
            "expected_evidence": [
                _claim("bogus_dimension", "category", "衬衫"),
            ],
        }
    ]
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid dimension"):
        load_cases(path)


def test_match_case_scores_exact_and_paraphrase_misses() -> None:
    expected = [
        _claim("garment", "category", "衬衫"),
        _claim("garment", "color", "黑色", polarity="negative"),
    ]
    actual = [
        _claim("garment", "category", "衬衫"),
        _claim("garment", "color", "黑色", polarity="negative"),
        _claim("garment", "fit", "宽松"),  # extra -> fp
    ]
    metrics = match_case(actual, expected)
    assert metrics["tp"] == 2
    assert metrics["fp"] == 1
    assert metrics["fn"] == 0
    assert metrics["precision"] == pytest.approx(2 / 3, abs=1e-3)
    assert metrics["recall"] == 1.0
    assert metrics["polarity_agreement"] == 1.0

    # A paraphrased value ("黑裤子" instead of "黑色") is scored as a miss.
    paraphrased = [
        _claim("garment", "category", "衬衫"),
        _claim("garment", "color", "黑裤子", polarity="negative"),
    ]
    metrics2 = match_case(paraphrased, expected)
    assert metrics2["tp"] == 1
    assert metrics2["fn"] == 1


def test_match_case_scope_agreement() -> None:
    expected = [
        _claim("garment", "fit", "宽松", scope={"type": "contextual", "occasions": ["通勤"]}),
    ]
    right = [
        _claim("garment", "fit", "宽松", scope={"type": "contextual", "occasions": ["通勤", "上班"]}),
    ]
    wrong = [
        _claim("garment", "fit", "宽松", scope={"type": "global"}),
    ]
    assert match_case(right, expected)["scope_agreement"] == 1.0
    assert match_case(wrong, expected)["scope_agreement"] == 0.0


def test_scope_matches_contextual_needs_expected_occasions() -> None:
    expected = {"type": "contextual", "occasions": ["通勤"]}
    assert scope_matches({"type": "contextual", "occasions": ["通勤", "上班"]}, expected)
    assert not scope_matches({"type": "contextual", "occasions": ["周末"]}, expected)
    assert not scope_matches({"type": "global"}, expected)


def test_extract_normalizes_evidence_key() -> None:
    assert evidence_key({"dimension": "Garment", "attribute": "Category", "value": "  衬衫 "}) == (
        "garment",
        "category",
        "衬衫",
    )


def test_evaluate_pipeline_with_scripted_llm() -> None:
    """The extractor consumes the scripted responses; metrics aggregate per case."""
    cases = load_cases(CASES_PATH)
    script = [_evidence() for _ in cases]
    # mem-01: one exact hit so the pipeline produces a true positive.
    script[0] = _evidence(_claim("garment", "category", "衬衫", polarity="positive"))
    # mem-02: the three expected negatives, all exact.
    script[1] = _evidence(
        _claim("garment", "color", "黑色", polarity="negative"),
        _claim("garment", "category", "裤子", polarity="negative"),
        _claim("garment", "fit", "修身", polarity="negative"),
    )
    # mem-13: a habit-fit claim, exact.
    script[12] = _evidence(_claim("garment", "fit", "宽松", polarity="positive"))
    llm = ScriptedExtensionLlm(script)
    actual = [extract_language_evidence(llm, case["request"]) for case in cases]
    assert len(actual) == len(cases)
    assert len(llm.calls) == len(cases)
    # mem-01 hits its first expected claim -> tp >= 1 and no fp.
    assert match_case(actual[0], cases[0]["expected_evidence"])["tp"] == 1
    # mem-02 (index 1) matches all three expected negatives exactly.
    mem02 = match_case(actual[1], cases[1]["expected_evidence"])
    assert mem02["tp"] == 3 and mem02["fp"] == 0 and mem02["fn"] == 0
