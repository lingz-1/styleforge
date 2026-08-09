"""Semantic Retriever agent (Agent 1): request signature + multi-query plans.

Turns the user request, a wardrobe summary, and recent memory into the frozen
``Agent1Output`` contract: request_signature, three retrieval_plans, and
per-category candidate requirements. On LLM failure it degrades to a
deterministic signature built from the parsed ``TaskSpec``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from styleforge.agents.planner import PlannerAgent
from styleforge.core.schemas import TaskSpec
from styleforge.core.slots import base_slot
from styleforge.llm.client import LlmCallDiagnostics, LlmInvalidJson, LlmSchemaViolation, LlmUnavailable
from styleforge.llm.prompts import PROMPT_VERSION, build_agent1_prompt
from styleforge.llm.extension_prompts import (
    EXTENSION_PROMPT_VERSION,
    build_extension_agent1_prompt,
)
from styleforge.llm.schema import (
    Agent1Output,
    CandidateRequirements,
    RequestSignature,
    RetrievalPlan,
)
from styleforge.tools.candidate_pool import SLOT_TO_CATEGORY
from styleforge.models.agent_tasks import Agent1TaskOutput, ExtensionIntentEnrichment
from styleforge.models.context import ContextPack
from styleforge.models.task import TaskExecutionInput
from styleforge.orchestration.task_router import TaskRoute
from styleforge.tools.extension_analysis import analyze_extension_task

DEFAULT_QUOTA = 50


class SemanticRetrieverAgent:
    """Agent 1 backend: LLM semantic retriever with deterministic fallback."""

    backend = "llm"

    def run_extension(
        self,
        *,
        database_path: Path,
        knowledge_root: Path,
        task_input: TaskExecutionInput,
        route: TaskRoute,
        context_pack: ContextPack,
        llm: Any,
    ) -> tuple[Agent1TaskOutput, dict[str, Any], LlmCallDiagnostics]:
        """Run Agent 1 strictly for an extension task, without fallback."""
        if llm is None:
            raise LlmUnavailable("extension Agent 1 requires a configured LLM client")
        tool_output = analyze_extension_task(
            database_path=database_path,
            knowledge_root=knowledge_root,
            task_input=task_input,
            route=route,
            context_pack=context_pack,
        )
        system, user = build_extension_agent1_prompt(
            request=task_input.request,
            context_pack=context_pack,
            tool_output=tool_output,
        )
        payload, diagnostics = llm.chat_json(
            system=system,
            user=user,
            json_schema=ExtensionIntentEnrichment.model_json_schema(),
        )
        try:
            enrichment = ExtensionIntentEnrichment.model_validate(payload)
        except ValidationError as error:
            raise LlmSchemaViolation(f"extension Agent 1 schema violation: {error}") from error

        resolved_target = dict(tool_output.resolved_target)
        if enrichment.target_style:
            resolved_target["interpreted_style"] = enrichment.target_style
        if enrichment.target_occasion:
            resolved_target["interpreted_occasion"] = enrichment.target_occasion
        if enrichment.target_item_terms:
            resolved_target["interpreted_item_terms"] = enrichment.target_item_terms
        facts = dict(tool_output.facts)
        if enrichment.optional_context:
            facts["agent1_optional_context"] = enrichment.optional_context
        output = tool_output.model_copy(
            update={
                "intent_summary": enrichment.intent_summary.strip()
                or tool_output.intent_summary,
                "resolved_target": resolved_target,
                "facts": facts,
                "needs_clarification": tool_output.needs_clarification,
                "clarification_question": tool_output.clarification_question,
            }
        )
        return output, {
            "degraded": False,
            "prompt_version": EXTENSION_PROMPT_VERSION,
            "diagnostics": diagnostics.to_dict(),
        }, diagnostics

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
