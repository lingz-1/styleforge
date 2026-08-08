"""Semantic Retriever agent (Agent 1): request signature + multi-query plans.

Turns the user request, a wardrobe summary, and recent memory into the frozen
``Agent1Output`` contract: request_signature, three retrieval_plans, and
per-category candidate requirements. On LLM failure it degrades to a
deterministic signature built from the parsed ``TaskSpec``.
"""

from __future__ import annotations

from typing import Any

from styleforge.agents.planner import PlannerAgent
from styleforge.core.schemas import TaskSpec
from styleforge.core.slots import base_slot
from styleforge.llm.client import LlmCallDiagnostics, LlmInvalidJson, LlmSchemaViolation, LlmUnavailable
from styleforge.llm.prompts import PROMPT_VERSION, build_agent1_prompt
from styleforge.llm.schema import (
    Agent1Output,
    CandidateRequirements,
    RequestSignature,
    RetrievalPlan,
)
from styleforge.tools.candidate_pool import SLOT_TO_CATEGORY

DEFAULT_QUOTA = 50


class SemanticRetrieverAgent:
    """Agent 1 backend: LLM semantic retriever with deterministic fallback."""

    backend = "llm"

    def run(
        self,
        *,
        user_query: str,
        task: TaskSpec,
        wardrobe_summary: dict[str, Any],
        recent_memories: list[dict[str, Any]],
        llm: Any,
        weights: dict[str, float] | None = None,
    ) -> tuple[Agent1Output, dict[str, Any], LlmCallDiagnostics | None]:
        if llm is None:
            output = deterministic_signature(task, user_query)
            info = {
                "degraded": True,
                "reason": "llm client unavailable",
                "prompt_version": PROMPT_VERSION,
            }
            return output, info, None
        try:
            system, user = build_agent1_prompt(
                user_query=user_query,
                wardrobe_summary=wardrobe_summary,
                recent_memories=recent_memories,
                weights=weights,
            )
            payload, diagnostics = llm.chat_json(
                system=system,
                user=user,
                json_schema={},
            )
            output = Agent1Output.model_validate(payload)
            info = {
                "degraded": False,
                "prompt_version": PROMPT_VERSION,
                "diagnostics": diagnostics.to_dict(),
            }
            return output, info, diagnostics
        except (LlmUnavailable, LlmInvalidJson, LlmSchemaViolation) as error:
            output = deterministic_signature(task, user_query)
            info = {
                "degraded": True,
                "reason": f"{type(error).__name__}: {error}",
                "prompt_version": PROMPT_VERSION,
            }
            return output, info, None


def _default_requirements(task: TaskSpec) -> CandidateRequirements:
    categories: dict[str, int] = {
        "tops": 0,
        "bottoms": 0,
        "dresses": 0,
        "outerwear": 0,
        "shoes": 0,
        "accessories": 0,
    }
    slots = [base_slot(slot) for slot in task.required_slots]
    if not slots:
        slots = ["top", "bottom", "footwear"]
    per = DEFAULT_QUOTA // len(slots)
    remaining = DEFAULT_QUOTA - per * len(slots)
    for index, slot in enumerate(slots):
        category = SLOT_TO_CATEGORY.get(slot)
        if category is None:
            continue
        categories[category] = per + (1 if index < remaining else 0)
    return CandidateRequirements(**categories)


def deterministic_signature(task: TaskSpec, user_query: str) -> Agent1Output:
    """Build a valid Agent1Output from the parsed task without any LLM call."""
    planner = PlannerAgent()
    theme = (user_query or "").strip() or task.occasion
    practical = [task.occasion] if task.occasion and task.occasion != "daily" else []
    signature = RequestSignature(
        theme=theme[:120],
        explicit_style=[],
        unique_mood=[],
        practical_context=practical,
        generic_tendencies_to_avoid=["仅由基础款组成，缺少能承载主题的视觉重点"],
    )

    slots = list(task.required_slots)
    if not slots:
        slots = ["top", "bottom", "footwear"]
    core_query = planner.retrieval_prompt(task, slots[0]) if slots else "versatile outfit"
    audience = " or ".join(task.target_audiences) or ""
    distinctive_query = (
        f"{audience} {task.occasion} distinctive statement outfit".strip()
        if task.occasion != "daily"
        else "distinctive fashion-forward statement piece"
    )
    supporting_query = (
        f"{audience} comfortable {task.occasion} supporting outfit".strip()
        if task.occasion != "daily"
        else "comfortable versatile everyday outfit"
    )
    plans = [
        RetrievalPlan(type="core", query=core_query, score_weight=0.40),
        RetrievalPlan(type="distinctive", query=distinctive_query, score_weight=0.30),
        RetrievalPlan(type="supporting", query=supporting_query, score_weight=0.10),
    ]
    return Agent1Output(
        request_signature=signature,
        retrieval_plans=plans,
        candidate_requirements=_default_requirements(task),
    )
