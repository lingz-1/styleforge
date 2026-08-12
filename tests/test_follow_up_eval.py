"""Gate tests for the follow-up detector evaluation harness.

The detector (``is_follow_up``) is pure, so the full curated corpus runs offline
and the gate asserts the intended behavior stays locked. If a future prompt or
routing change drifts the detector, this test fails first.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.runners.evaluate_follow_up import evaluate_follow_up, load_cases
from styleforge.workflow.task_workflow import is_follow_up


CASES_PATH = Path("evals/cases/follow_up.json")


def test_case_file_is_valid_and_balanced() -> None:
    cases = load_cases(CASES_PATH)
    assert len(cases) >= 50
    counts = {"follow_up": 0, "fresh": 0}
    for case in cases:
        counts[case["expected"]] += 1
        assert case.get("rule"), f"{case['id']} is missing a rule annotation"
    # Both classes must be substantial for the rates to be meaningful.
    assert counts["follow_up"] >= 25
    assert counts["fresh"] >= 25


def test_case_loader_rejects_invalid_label(tmp_path: Path) -> None:
    bad = [{"id": "x", "request": "更正式一点", "expected": "maybe"}]
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid expected label"):
        load_cases(path)


def test_case_loader_rejects_empty_request(tmp_path: Path) -> None:
    bad = [{"id": "x", "request": "  ", "expected": "fresh"}]
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="empty request"):
        load_cases(path)


def test_curated_corpus_reaches_full_accuracy() -> None:
    """The intent-labeled (non-gap) corpus is the ground truth: the detector must
    agree on every one of them; known-gap cases are excluded and listed."""
    report = evaluate_follow_up(CASES_PATH)
    assert report["case_count"] == len(load_cases(CASES_PATH))
    assert report["correct_count"] == report["case_count"] - report["known_gap_count"]
    assert report["metrics"]["accuracy"] == 1.0
    assert report["metrics"]["false_positive_follow_up_rate"] == 0.0
    assert report["metrics"]["missed_follow_up_rate"] == 0.0
    assert report["failures"] == []


def test_known_gap_cases_are_real_detector_deviations() -> None:
    """Every known_gap case must actually deviate from intent — they document
    the detector's blind spots instead of silently passing."""
    cases = load_cases(CASES_PATH)
    gaps = [c for c in cases if c.get("known_gap")]
    assert len(gaps) >= 4
    for case in gaps:
        actual = "follow_up" if is_follow_up(case["request"]) else "fresh"
        assert actual != case["expected"], (
            f"{case['id']} is marked known_gap but the detector agrees with intent"
        )
    # The gap classes are the interesting ones: addition signals, direction
    # verbs, and style-word false positives on fresh briefs.
    rules = {case["rule"] for case in gaps}
    assert "known_gap_addition_signal" in rules
    assert "known_gap_fresh_style_word_false_positive" in rules


def test_fresh_word_overrides_adjust_word() -> None:
    """A fresh-brief marker beats an adjustment word: 更正式 + 搭配 -> fresh."""
    cases = {case["id"]: case for case in load_cases(CASES_PATH)}
    fresh_word_case = cases["fr-12"]
    assert fresh_word_case["expected"] == "fresh"
    assert not is_follow_up(fresh_word_case["request"])


def test_slot_adjustments_are_follow_ups() -> None:
    """Every slot-replacement case in the corpus is a follow-up."""
    cases = load_cases(CASES_PATH)
    for case in cases:
        if case.get("rule") == "slot_replacement":
            assert case["expected"] == "follow_up"
            assert is_follow_up(case["request"])
