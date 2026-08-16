"""Known-value tests for the dependency-free paired-statistics analysis layer.

Locks the pure-python statistical primitives in
``evals/analysis/analyze_p_outfit.py`` (t-CDF, inverse t, Wilcoxon, exact
McNemar, Cohen's d / mean-diff CI) against hand-computed values, so a silent
regression in the math cannot skew the order-vs-image significance layer.
"""

from __future__ import annotations

import pytest

from evals.analysis.analyze_p_outfit import (
    analyze_report,
    cohens_d_ci,
    mcnemar_exact,
    paired_t_test,
    student_t_cdf,
    t_critical_two_sided,
    wilcoxon_signed_rank,
)


def test_student_t_cdf_known_tails() -> None:
    # Two-sided tail probabilities from the standard t table.
    assert student_t_cdf(1.96, 10000) == pytest.approx(0.05, abs=1e-3)
    assert student_t_cdf(12.706, 1) == pytest.approx(0.05, abs=1e-3)
    assert student_t_cdf(2.776, 4) == pytest.approx(0.05, abs=1e-3)


def test_t_critical_known_values() -> None:
    assert t_critical_two_sided(0.05, 99) == pytest.approx(1.984, abs=1e-3)
    assert t_critical_two_sided(0.05, 1) == pytest.approx(12.706, abs=1e-3)
    assert t_critical_two_sided(0.05, 4) == pytest.approx(2.776, abs=1e-3)


def test_paired_t_test_known() -> None:
    # All-positive constant differences -> large negative t -> tiny p.
    result = paired_t_test([1, 2, 3, 4, 5])
    assert result["n"] == 5 and result["df"] == 4
    # mean=3, sample std=sqrt(2.5), se=0.7071 -> t = 3/0.7071.
    assert result["t"] == pytest.approx(4.2426, abs=1e-3)
    assert result["p_value"] < 0.02


def test_wilcoxon_known() -> None:
    result = wilcoxon_signed_rank([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    assert result["W_plus"] == 55.0
    assert result["n"] == 10
    assert result["z"] == pytest.approx(2.8031, abs=1e-3)
    assert result["p_value"] == pytest.approx(0.00506, abs=1e-3)


def test_mcnemar_exact_known() -> None:
    # b=5, c=0 -> 2 * P(X=0 | Binom(5, .5)) = 2 * 0.5^5.
    assert mcnemar_exact(5, 0)["p_value"] == pytest.approx(0.0625, abs=1e-6)
    assert mcnemar_exact(0, 0)["p_value"] == 1.0
    assert mcnemar_exact(2, 2)["p_value"] == 1.0  # perfectly balanced


def test_cohens_d_constant_zero() -> None:
    result = cohens_d_ci([2.0, 2.0, 2.0, 2.0])
    assert result["d"] == 0.0
    assert result["mean_ci_low"] == pytest.approx(2.0, abs=1e-6)
    assert result["mean_ci_high"] == pytest.approx(2.0, abs=1e-6)


def _case(case_id: str, judge: float, passed: bool, dims: dict) -> dict:
    return {
        "id": case_id, "decision": "accept", "judge_overall": judge,
        "judge_overall_golden": judge + 10.0, "judge_failed": False,
        "judge_dimensions": dims, "gap": False, "passed": passed,
        "violations": [], "critic_score": judge, "latency_seconds": 1.0,
        "llm_call_count": 3,
    }


def test_analyze_report_structure() -> None:
    dims = {"request_relevance": 8, "request_specificity": 8,
            "outfit_coordination": 8, "wearability": 8, "freshness": 8}
    report = {
        "schema_version": "styleforge.p-outfit-eval.v1",
        "runner_version": "evaluate_p_outfit.v1",
        "modes": {
            "order": {
                "metrics": {"mean_judge_overall": 60.0, "pass_rate": 1.0,
                            "hard_violation_rate": 0.0, "gap_rate": 0.0},
                "per_case": [
                    _case("c1", 60.0, True, dims), _case("c2", 70.0, True, dims),
                    _case("c3", 80.0, True, dims),
                ],
            },
            "image": {
                "metrics": {"mean_judge_overall": 65.0, "pass_rate": 1.0,
                            "hard_violation_rate": 0.0, "gap_rate": 0.0},
                "per_case": [
                    _case("c1", 63.0, True, dims), _case("c2", 73.0, True, dims),
                    _case("c3", 83.0, True, dims),
                ],
            },
        },
    }
    analysis = analyze_report(report)
    assert analysis["n_cases_paired"] == 3
    assert analysis["paired_judge_overall"]["mean_delta_image_minus_order"] == 3.0
    t_result = analysis["paired_judge_overall"]["paired_t_test"]
    assert t_result["df"] == 2 and t_result["n"] == 3
    assert analysis["paired_dimension_deltas"]["wearability"] == 0.0
    # perfectly matched pass flags -> McNemar has zero discordant pairs.
    assert analysis["mcnemar"]["passed"]["n"] == 0
    assert analysis["mcnemar"]["passed"]["p_value"] == 1.0
