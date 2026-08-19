"""Goal Gate: have we produced enough candidates? (Harness node).

A Main-Graph node. ``enough_candidates`` becomes the route decision after the
next candidate is staged: not enough → ResetCandidateDraft → Stylist (next
parallel candidate); enough → end. In H2 the "enough → CONTINUE" arm routes to
the Coordinator instead.
"""

from __future__ import annotations

from typing import Any


def make_goal_gate(target_candidates: int):
    """A Main-Graph node over a fixed target candidate count."""

    def goal_gate(state: dict[str, Any]) -> dict[str, Any]:
        count = len(state.get("candidates") or [])
        return {"enough_candidates": count >= target_candidates}

    return goal_gate
