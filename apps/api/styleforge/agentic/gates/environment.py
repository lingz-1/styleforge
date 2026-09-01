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
from styleforge.agentic.intent_constraints import missing_feature_requirements


def make_environment_gate(environment: Environment):
    """A Main-Graph node over one Environment (runtime dep, never in state)."""

    def environment_gate(state: dict[str, Any]) -> dict[str, Any]:
        draft = state.get("working_draft")
        if draft is None:
            return {
                "environment_valid": False,
                "intent_constraint_failed": False,
                "intent_constraint_issues": [],
                "gate_feedback": "没有可校验的候选（working_draft 为空）",
            }
        # An empty draft is physically "valid" to check_environment but is not a
        # usable candidate — the Stylist must replan, never let an empty outfit
        # slip through to the Critic and get staged.
        if not draft.outfit.item_ids:
            return {
                "environment_valid": False,
                "intent_constraint_failed": False,
                "intent_constraint_issues": [],
                "gate_feedback": "候选为空：还没有任何单品，请重新组合",
            }
        result = environment.check_environment(draft)
        if not result.valid:
            return {
                "environment_valid": False,
                "intent_constraint_failed": False,
                "intent_constraint_issues": [],
                "gate_feedback": "物理校验未通过：" + "；".join(result.issues),
            }

        if str(state.get("task_type") or "") != "outfit_recommend":
            return {
                "environment_valid": True,
                "intent_constraint_failed": False,
                "intent_constraint_issues": [],
            }

        wardrobe_items_for = getattr(environment, "wardrobe_items_for", None)
        if wardrobe_items_for is None:
            # Lightweight test/third-party environments may only implement the
            # original physical gate contract. With no authoritative feature
            # metadata, semantic enforcement must degrade to the Critic.
            return {
                "environment_valid": True,
                "intent_constraint_failed": False,
                "intent_constraint_issues": [],
            }
        selected = wardrobe_items_for(list(draft.outfit.item_ids))
        missing = missing_feature_requirements(
            str(state.get("request") or ""),
            selected,
            available_items=environment.wardrobe_items,
        )
        if missing:
            details = [
                f"{requirement.reason}需要"
                f"{(' ' + requirement.slot) if requirement.slot else ''} 特征 {requirement.feature}"
                for requirement in missing
            ]
            return {
                "environment_valid": False,
                "intent_constraint_failed": True,
                "intent_constraint_issues": details,
                "gate_feedback": "明确需求校验未通过：" + "；".join(details),
            }
        return {
            "environment_valid": True,
            "intent_constraint_failed": False,
            "intent_constraint_issues": [],
        }

    return environment_gate
