"""Paired statistical analysis of the p-outfit order-vs-image comparison.

Reads a finished ``evaluate_p_outfit`` report (``artifacts/evaluation/
p_outfit.json``), pairs every case across the ``order`` and ``image`` modes by
case id, and computes the significance layer the runner deliberately defers to
an analysis stage (the report's ``comparison`` is descriptive only):

- paired t-test and Wilcoxon signed-rank on ``judge_overall`` deltas
- Cohen's d (paired) with a t-based 95% confidence interval
- exact McNemar tests on ``passed`` / ``hard_violation`` / ``gap`` flags
- per-dimension mean judge deltas

Everything is dependency-free (``math`` / ``statistics``), so it runs in the
bare style env. All p-values are two-sided.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

# --- statistics primitives --------------------------------------------------

# Lanczos log-gamma (enough precision for n up to a few hundred).
_LANCZOS = (
    676.5203681218851, -1259.1392167224028, 771.32342877765313,
    -176.61502916214059, 12.507343278686905, -0.13857109526572012,
    9.9843695780195716e-6, 1.5056327351493116e-7,
)


def _log_gamma(x: float) -> float:
    if x < 0.5:
        return math.log(math.pi) - math.log(math.sin(math.pi * x)) - _log_gamma(1.0 - x)
    x -= 1.0
    a = 0.99999999999980993
    t = x + 7.5
    for i, coef in enumerate(_LANCZOS):
        a += coef / (x + i + 1)
    return 0.5 * math.log(2 * math.pi) + (x + 0.5) * math.log(t) - t + math.log(a)


def _betacf(a: float, b: float, x: float) -> float:
    fpm = 1e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpm:
        d = fpm
    d = 1.0 / d
    h = d
    for m in range(1, 201):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpm:
            d = fpm
        c = 1.0 + aa / c
        if abs(c) < fpm:
            c = fpm
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpm:
            d = fpm
        c = 1.0 + aa / c
        if abs(c) < fpm:
            c = fpm
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-12:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(
        _log_gamma(a + b) - _log_gamma(a) - _log_gamma(b)
        + a * math.log(x) + b * math.log(1.0 - x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def student_t_cdf(t: float, df: int) -> float:
    """Two-sided P(|T| > |t|) for Student's t with df degrees of freedom."""
    x = df / (df + t * t)
    return _betai(0.5 * df, 0.5, x)


def t_critical_two_sided(alpha: float, df: int) -> float:
    """t value where the two-sided tail probability equals alpha (p is
    decreasing in t, so a p(mid) >= alpha means mid is too small -> raise lo)."""
    lo, hi = 0.0, 30.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if student_t_cdf(mid, df) >= alpha:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _average_ranks(values: list[float]) -> list[float]:
    ordered = sorted(values)
    ranks: dict[float, float] = {}
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1] == ordered[i]:
            j += 1
        avg = 0.5 * (i + j) + 1.0
        for k in range(i, j + 1):
            ranks[ordered[k]] = avg
        i = j + 1
    return [ranks[v] for v in values]


def wilcoxon_signed_rank(diffs: list[float]) -> dict[str, float]:
    """Two-sided Wilcoxon signed-rank test with a normal approximation."""
    nonzero = [d for d in diffs if d != 0.0]
    n = len(nonzero)
    if n == 0:
        return {"n": 0, "W_plus": 0.0, "z": None, "p_value": 1.0}
    ranks = _average_ranks([abs(d) for d in nonzero])
    w_plus = sum(r for d, r in zip(nonzero, ranks, strict=True) if d > 0)
    mean = n * (n + 1) / 4.0
    std = math.sqrt(n * (n + 1) * (2 * n + 1) / 24.0)
    z = (w_plus - mean) / std if std else 0.0
    return {"n": n, "W_plus": round(w_plus, 3), "z": round(z, 4),
            "p_value": round(2 * (1.0 - normal_cdf(abs(z))), 6)}


def paired_t_test(diffs: list[float]) -> dict[str, float]:
    n = len(diffs)
    if n < 2:
        return {"n": n, "t": None, "df": 0, "p_value": 1.0}
    mean = statistics.fmean(diffs)
    std = statistics.stdev(diffs) if n >= 2 else 0.0
    if std == 0.0:
        return {"n": n, "t": None, "df": n - 1, "p_value": 1.0}
    t = mean / (std / math.sqrt(n))
    p = student_t_cdf(t, n - 1)
    return {"n": n, "t": round(t, 4), "df": n - 1, "p_value": round(p, 6)}


def cohens_d_ci(diffs: list[float], alpha: float = 0.05) -> dict[str, float]:
    n = len(diffs)
    if n < 2:
        return {"d": None, "ci_low": None, "ci_high": None}
    mean = statistics.fmean(diffs)
    std = statistics.stdev(diffs)
    d = mean / std if std else 0.0
    # t-based CI of the mean difference (not the d distribution).
    se = std / math.sqrt(n)
    tc = t_critical_two_sided(alpha, n - 1)
    return {
        "d": round(d, 4),
        "mean_ci_low": round(mean - tc * se, 3),
        "mean_ci_high": round(mean + tc * se, 3),
    }


def mcnemar_exact(discordant_ab: int, discordant_ba: int) -> dict[str, float]:
    """Exact two-sided McNemar on the discordant-pair counts."""
    n = discordant_ab + discordant_ba
    if n == 0:
        return {"n": 0, "ab": 0, "ba": 0, "p_value": 1.0}
    k = min(discordant_ab, discordant_ba)
    tail = sum(
        math.comb(n, i) * 0.5 ** n for i in range(k + 1)
    )
    return {
        "n": n,
        "ab": discordant_ab,
        "ba": discordant_ba,
        "p_value": round(min(2.0 * tail, 1.0), 6),
    }


# --- analysis ------------------------------------------------------------------

def _paired_records(report: dict) -> list[dict]:
    order = {c["id"]: c for c in report["modes"]["order"]["per_case"]}
    image = {c["id"]: c for c in report["modes"]["image"]["per_case"]}
    pairs = []
    for case_id in order:
        if case_id in image:
            pairs.append({"id": case_id, "order": order[case_id], "image": image[case_id]})
    return pairs


def _numeric_diff(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else b - a  # image - order


def analyze_report(report: dict) -> dict:
    pairs = _paired_records(report)
    n_cases = len(pairs)

    def scored(key: str) -> list[float]:
        return [
            p["image"][key] - p["order"][key]
            for p in pairs
            if p["order"][key] is not None and p["image"][key] is not None
        ]

    judge_deltas = scored("judge_overall")
    dims = ("request_relevance", "request_specificity", "outfit_coordination",
            "wearability", "freshness")

    def dim_delta(key: str) -> list[float]:
        out = []
        for p in pairs:
            od = p["order"].get("judge_dimensions") or {}
            gd = p["image"].get("judge_dimensions") or {}
            if key in od and key in gd:
                out.append(gd[key] - od[key])
        return out

    def flag_discordant(field: str, is_flagged) -> tuple[int, int]:
        ab = ba = 0
        for p in pairs:
            o = is_flagged(p["order"], field)
            g = is_flagged(p["image"], field)
            if o and not g:
                ab += 1
            elif g and not o:
                ba += 1
        return ab, ba

    def _flagged(case: dict, field: str) -> bool:
        return bool(case.get(field))

    def _violated(case: dict, field: str) -> bool:
        return bool(case.get(field))

    def mean(values: list[float]) -> float | None:
        return round(statistics.fmean(values), 3) if values else None

    order_metrics = report["modes"]["order"]["metrics"]
    image_metrics = report["modes"]["image"]["metrics"]

    return {
        "schema_version": report["schema_version"],
        "runner_version": report["runner_version"],
        "n_cases_paired": n_cases,
        "n_scored_deltas": len(judge_deltas),
        "mode_summary": {
            "order": {"mean_judge_overall": order_metrics["mean_judge_overall"],
                      "pass_rate": order_metrics["pass_rate"],
                      "hard_violation_rate": order_metrics["hard_violation_rate"],
                      "gap_rate": order_metrics["gap_rate"]},
            "image": {"mean_judge_overall": image_metrics["mean_judge_overall"],
                      "pass_rate": image_metrics["pass_rate"],
                      "hard_violation_rate": image_metrics["hard_violation_rate"],
                      "gap_rate": image_metrics["gap_rate"]},
        },
        "paired_judge_overall": {
            "mean_delta_image_minus_order": mean(judge_deltas),
            "std_delta": round(statistics.stdev(judge_deltas), 3) if len(judge_deltas) >= 2 else None,
            "paired_t_test": paired_t_test(judge_deltas),
            "wilcoxon": wilcoxon_signed_rank(judge_deltas),
            "cohens_d": cohens_d_ci(judge_deltas),
        },
        "paired_dimension_deltas": {
            dim: mean(dim_delta(dim)) for dim in dims
        },
        "mcnemar": {
            "passed": mcnemar_exact(*flag_discordant("passed", _flagged)),
            "hard_violation": mcnemar_exact(*flag_discordant("violations", _violated)),
            "gap": mcnemar_exact(*flag_discordant("gap", _flagged)),
        },
        "interpretation": {
            "judge_independence": (
                "consistent critic-vs-judge gap with low correlation is expected; "
                "see critic_judge_consistency in each mode"
            ),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path,
                        default=Path("artifacts/evaluation/p_outfit.json"))
    parser.add_argument("--out", type=Path,
                        default=Path("artifacts/evaluation/p_outfit_analysis.json"))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    analysis = analyze_report(report)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(analysis, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
