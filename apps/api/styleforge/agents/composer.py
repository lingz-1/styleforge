"""Composer agent (Agent 2): compose 3-5 outfits from the candidate pool.

The composer picks item IDs from the pool only. ``sanitize_pool_ids`` drops
any proposal containing an out-of-pool ID (first layer of defense; the rule
validator re-checks independently). On LLM failure it degrades to the
deterministic candidate generator over the same pool.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from styleforge.core.decision import derive_decision_from_facts
from styleforge.core.schemas import CatalogItem, TaskSpec
from styleforge.llm.extension_prompts import (
    EXTENSION_PROMPT_VERSION,
    agent2_llm_schema,
    build_extension_agent2_prompt,
)
from styleforge.llm.client import LlmCallDiagnostics, LlmInvalidJson, LlmSchemaViolation, LlmUnavailable
from styleforge.llm.prompts import PROMPT_VERSION, build_agent2_prompt
from styleforge.llm.schema import (
    Agent2Output,
    CompositionStrategy,
    EnvironmentAdjustment,
    OutfitProposal,
    parse_llm_json,
)
from styleforge.models.agent_tasks import Agent1TaskOutput, Agent2TaskOutput
from styleforge.models.context import ContextPack
from styleforge.models.task_results import validate_task_result
from styleforge.orchestration.task_router import TaskType
from styleforge.tools.candidate_generation import generate_candidates, select_diverse_candidates
from styleforge.tools.extension_validation import sanitize_extension_references


def deterministic_strategy(task: TaskSpec) -> CompositionStrategy:
    return CompositionStrategy(
        visual_anchor=f"{task.occasion} 主题的视觉重点单品",
        supporting_direction="与主视觉协调的支撑单品",
        practical_balance="在满足实穿性前提下表达场合氛围",
    )


def sanitize_pool_ids(
    proposals: list[OutfitProposal],
    pool_ids: set[str],
) -> list[OutfitProposal]:
    """Drop proposals that reference any item outside the candidate pool."""
    return [proposal for proposal in proposals if set(proposal.item_ids) <= pool_ids]


def sanitize_environment_fields(
    proposal: OutfitProposal,
) -> tuple[OutfitProposal, int]:
    """Drop ungrounded environment adjustments from a single proposal.

    An adjustment is kept only when it has at least one fact reference and
    every referenced wardrobe item actually appears in the proposal's
    ``item_ids``. Returns ``(sanitized_proposal, dropped_count)``.
    """
    item_set = set(proposal.item_ids)
    kept: list[EnvironmentAdjustment] = []
    dropped = 0
    for adjustment in proposal.environment_adjustments:
        if not adjustment.fact_refs:
            dropped += 1
            continue
        if not set(adjustment.wardrobe_item_ids) <= item_set:
            dropped += 1
            continue
        kept.append(adjustment)
    if dropped:
        proposal = proposal.model_copy(update={"environment_adjustments": kept})
    return proposal, dropped


def deterministic_composition(
    *,
    pool_items: list[CatalogItem],
    task: TaskSpec,
    pool_scores: dict[str, float],
) -> tuple[list[OutfitProposal], dict[str, Any]]:
    candidates, generation_diagnostics = generate_candidates(
        wardrobe_items=pool_items,
        task=task,
        per_slot_limit=50,
        max_candidates=2000,
        item_relevance=pool_scores,
    )
    selected = select_diverse_candidates(
        candidates,
        max(task.max_results, 3),
        max_jaccard_similarity=0.49,
    )
    proposals: list[OutfitProposal] = []
    for candidate in selected:
        proposals.append(
            OutfitProposal(
                outfit_id=candidate.outfit_id,
                composition_strategy=deterministic_strategy(task),
                item_ids=list(candidate.item_ids),
                style_tag=task.occasion,
                reasoning="；".join(candidate.reasons[:3]),
                request_specific_elements=[],
            )
        )
    return proposals, generation_diagnostics


class ComposerAgent:
    """Agent 2 backend: LLM composer with deterministic fallback."""

    backend = "llm"

    def run_extension(
        self,
        *,
        user_query: str,
        context_pack: ContextPack,
        agent1_output: Agent1TaskOutput,
        llm: Any,
        critic_feedback: str = "",
        repair_feedback: str = "",
    ) -> tuple[Agent2TaskOutput, dict[str, Any], LlmCallDiagnostics]:
        """Run Agent 2 strictly for an extension task, without fallback.

        ``repair_feedback`` carries a hard-validation failure from a later node
        (duplicate core slots, too few alternatives) so a retry here can fix it;
        it is folded into the prompt alongside any internal schema-repair note.
        """
        if llm is None:
            raise LlmUnavailable("extension Agent 2 requires a configured LLM client")
        repair = repair_feedback or ""
        # PR4A: the decision is a factual classification of the feasibility
        # facts, derived here so the prompt can carry it and the output can
        # carry it -- never guessed by the LLM.
        decision = derive_decision_from_facts(agent1_output.facts)
        call_diagnostics: list[LlmCallDiagnostics] = []
        for attempt in range(2):
            system, user = build_extension_agent2_prompt(
                request=user_query,
                context_pack=context_pack,
                agent1_output=agent1_output,
                critic_feedback=critic_feedback,
                repair_feedback=repair,
                decision=decision,
            )
            payload, diagnostics = llm.chat_json(
                system=system,
                user=user,
                json_schema=agent2_llm_schema(),
            )
            call_diagnostics.append(diagnostics)
            try:
                output = Agent2TaskOutput.model_validate(payload)
                if output.task_type is not agent1_output.task_type:
                    raise ValueError("task_type differs from Agent 1")
                if output.status == "needs_clarification" and not agent1_output.needs_clarification:
                    raise ValueError(
                        "needs_clarification is forbidden because Agent 1 resolved all required context"
                    )
                result = validate_task_result(output.task_type, output.result)
                if result["status"] != output.status:
                    raise ValueError("result.status differs from top-level status")
                output = output.model_copy(update={"result": result})
                # Boundary cleanup: drop out-of-scope references (LLM may fill
                # same-series variants that were never in Agent 1's candidate
                # scope). Re-validate afterwards because pruning can empty a
                # slot or drop the only sample outfit.
                output = sanitize_extension_references(agent1_output, output)
                try:
                    result = validate_task_result(output.task_type, output.result)
                    output = output.model_copy(update={"result": result})
                except (ValidationError, ValueError) as error:
                    if attempt == 0:
                        repair = "\n".join(
                            part
                            for part in (
                                repair_feedback or "",
                                "上次草稿引用了 Agent 1 候选范围外的单品 ID，清理后无法满足完成条件。"
                                "请严格只使用 Agent 1 的 candidate_item_ids 中的单品，"
                                "不得联想同系列或同款不同色的单品。"
                                f"校验失败：{error}",
                            )
                            if part
                        )
                        continue
                    raise LlmSchemaViolation(
                        f"extension Agent 2 result invalid after boundary cleanup: {error}"
                    ) from error
                break
            except (ValidationError, ValueError, KeyError) as error:
                if attempt == 0:
                    repair = "\n".join(
                        part
                        for part in (
                            repair_feedback or "",
                            f"上次草稿未满足任务完成契约：{error}",
                        )
                        if part
                    )
                    continue
                raise LlmSchemaViolation(
                    f"extension Agent 2 schema violation after repair: {error}"
                ) from error
        # Deterministic decision wins: even if the LLM echoed some value, the
        # decision field is owned by the fact-derived classification.
        if decision is not None:
            output = output.model_copy(update={"decision": decision})
        # The partition of the current outfit (locked vs replaced) is Agent 1's
        # deterministic fact, never the LLM's to decide. Restore both from the
        # facts so any deviation cannot trip the hard validator. Flexible
        # rebuilds keep the LLM's own choice -- the flexible validator never
        # compares these two fields.
        if (
            output.task_type is TaskType.OUTFIT_MODIFY
            and (agent1_output.facts or {}).get("adjustment_mode") != "flexible"
        ):
            facts = agent1_output.facts or {}
            restored = validate_task_result(
                output.task_type,
                {
                    **output.result,
                    "locked_item_ids": list(facts.get("locked_item_ids", [])),
                    "replaced_item_ids": list(facts.get("replaced_item_ids", [])),
                },
            )
            output = output.model_copy(update={"result": restored})
        return output, {
            "degraded": False,
            "prompt_version": EXTENSION_PROMPT_VERSION,
            "diagnostics": diagnostics.to_dict(),
            "attempt_diagnostics": [item.to_dict() for item in call_diagnostics],
            "call_count": len(call_diagnostics),
        }, diagnostics

    def run(
        self,
        *,
        user_query: str,
        request_signature: dict[str, Any],
        pool_manifest: list[dict[str, Any]],
        recent_structure_signatures: list[dict[str, Any]],
        llm: Any,
        pool_ids: set[str],
        task: TaskSpec | None = None,
        pool_items: list[CatalogItem] | None = None,
        pool_scores: dict[str, float] | None = None,
        weights: dict[str, float] | None = None,
        environment_context: dict[str, Any] | None = None,
        memory_profile: Any = None,
    ) -> tuple[list[OutfitProposal], dict[str, Any], LlmCallDiagnostics | None]:
        if llm is None:
            return self._fallback(
                task,
                pool_items,
                pool_scores,
                LlmUnavailable("llm client unavailable"),
            )
        try:
            system, user = build_agent2_prompt(
                user_query=user_query,
                request_signature=request_signature,
                pool_manifest=pool_manifest,
                recent_structure_signatures=recent_structure_signatures,
                weights=weights,
                environment_context=environment_context,
                memory_profile=memory_profile,
            )
            payload, diagnostics = llm.chat_json(
                system=system,
                user=user,
                json_schema={},
            )
            output = parse_llm_json(json.dumps(payload), Agent2Output)
            proposals = sanitize_pool_ids(output.outfits, pool_ids)
            if not proposals:
                raise LlmInvalidJson("no proposal survived the pool-id check")
            sanitized_proposals: list[OutfitProposal] = []
            environment_dropped = 0
            for proposal in proposals:
                cleaned, dropped = sanitize_environment_fields(proposal)
                sanitized_proposals.append(cleaned)
                environment_dropped += dropped
            proposals = sanitized_proposals
            info = {
                "degraded": False,
                "prompt_version": PROMPT_VERSION,
                "diagnostics": diagnostics.to_dict(),
                "sanitized_dropped": len(output.outfits) - len(proposals),
                "environment_dropped": environment_dropped,
            }
            return proposals, info, diagnostics
        except (LlmUnavailable, LlmInvalidJson, LlmSchemaViolation) as error:
            return self._fallback(task, pool_items, pool_scores, error)

    def _fallback(
        self,
        task: TaskSpec | None,
        pool_items: list[CatalogItem] | None,
        pool_scores: dict[str, float] | None,
        error: Exception,
    ) -> tuple[list[OutfitProposal], dict[str, Any], None]:
        if task is None or not pool_items or pool_scores is None:
            # Cannot fall back without the deterministic inputs; surface the error.
            raise error
        proposals, generation_diagnostics = deterministic_composition(
            pool_items=pool_items,
            task=task,
            pool_scores=pool_scores,
        )
        info = {
            "degraded": True,
            "reason": f"{type(error).__name__}: {error}",
            "prompt_version": PROMPT_VERSION,
            "generation_diagnostics": generation_diagnostics,
        }
        return proposals, info, None
