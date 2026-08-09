"""Composer agent (Agent 2): compose 3-5 outfits from the candidate pool.

The composer picks item IDs from the pool only. ``sanitize_pool_ids`` drops
any proposal containing an out-of-pool ID (first layer of defense; the rule
validator re-checks independently). On LLM failure it degrades to the
deterministic candidate generator over the same pool.
"""

from __future__ import annotations

import json
from typing import Any

from styleforge.core.schemas import CatalogItem, TaskSpec
from styleforge.llm.client import LlmCallDiagnostics, LlmInvalidJson, LlmSchemaViolation, LlmUnavailable
from styleforge.llm.prompts import PROMPT_VERSION, build_agent2_prompt
from styleforge.llm.schema import Agent2Output, CompositionStrategy, OutfitProposal, parse_llm_json
from styleforge.tools.candidate_generation import generate_candidates, select_diverse_candidates


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
            info = {
                "degraded": False,
                "prompt_version": PROMPT_VERSION,
                "diagnostics": diagnostics.to_dict(),
                "sanitized_dropped": len(output.outfits) - len(proposals),
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
