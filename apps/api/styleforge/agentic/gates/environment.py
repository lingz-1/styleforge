"""Environment Gate: physical/structural validity of a candidate (Harness node).

The Stylist subgraph only *produces*; this Main-Graph node verifies the
candidate against the deterministic Environment (``check_environment``) before
the Critic ever sees it. The gate lives in the Main Graph — never inside a
subgraph — so every future candidate producer (Wardrobe, subagents) reuses it.
On failure it writes ``gate_feedback`` for the next candidate run.
"""

from __future__ import annotations

from typing import Any

from styleforge.agentic.environment import Environment


def make_environment_gate(environment: Environment):
    """A Main-Graph node over one Environment (runtime dep, never in state)."""

    def environment_gate(state: dict[str, Any]) -> dict[str, Any]:
        draft = state.get("working_draft")
        if draft is None:
            return {
                "environment_valid": False,
                "gate_feedback": "没有可校验的候选（working_draft 为空）",
            }
        # An empty draft is physically "valid" to check_environment but is not a
        # usable candidate — the Stylist must replan, never let an empty outfit
        # slip through to the Critic and get staged.
        if not draft.outfit.item_ids:
            return {
                "environment_valid": False,
                "gate_feedback": "候选为空：还没有任何单品，请重新组合",
            }
        result = environment.check_environment(draft)
        if result.valid:
            return {"environment_valid": True}
        return {
            "environment_valid": False,
            "gate_feedback": "物理校验未通过：" + "；".join(result.issues),
        }

    return environment_gate
