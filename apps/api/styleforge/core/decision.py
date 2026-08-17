"""Agent 2 decision: deterministic mapping from feasibility facts to a decision.

PR4A: Agent 2 consumes ``RequestSpec + CandidatePool + FeasibilityReport``
(the structured modify facts) and emits a *decision* before composing.  The
decision is derived here as a pure, database-free function -- LLM decides the
content of the outfits, but the situation (exact / relax-a-preference / need
more retrieval / ask / gap) is a factual classification, never left to the LLM.

The classification reads the *decomposed* facts from the relaxation plan, not
the coarse feasibility status alone:

  * ``unsatisfied_hard``  -- a MUST target has no candidate at *any* level of
                             its relaxation chain (``option.unmet``): a genuine
                             wardrobe gap -> WARDROBE_GAP.
  * ``relaxed_must``      -- a MUST target is exact-unmet but a MUST-level
                             relaxation (colour drop, or type broadened to its
                             slot) would unlock candidates.  MUST / MUST_NOT /
                             LOCK are never relaxed automatically -- *any* MUST
                             dimension (colour, type, slot, occasion) falls
                             here -> RETRIEVE_MORE (broader retrieval or user
                             confirmation first).
  * ``unsatisfied_soft``  -- only a PREFER colour is unmet while every MUST is
                             exactly satisfied -> RELAX_PREFERENCE, surrendering
                             *only* that preference.
  * an unresolved anaphor -> ASK_USER; nothing unmet -> EXACT_MATCH.

Invariant locked by this layer (Concern A, generalised): no MUST / MUST_NOT /
LOCK constraint is ever relaxed by the decision itself.  The only thing a
decision may give up is a PREFER colour.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, Field

from styleforge.core.candidate_service import FeasibilityState
from styleforge.core.relaxation import RelaxationPlan


class DecisionType(str, Enum):
    EXACT_MATCH = "EXACT_MATCH"
    RELAX_PREFERENCE = "RELAX_PREFERENCE"
    RETRIEVE_MORE = "RETRIEVE_MORE"
    ASK_USER = "ASK_USER"
    WARDROBE_GAP = "WARDROBE_GAP"


class Agent2DecisionFacts(BaseModel):
    """The minimal feasibility facts the decision needs.

    Just the feasibility status plus the per-MUST relaxation chains.  Both are
    already surfaced inside ``feasibility_report`` (``state`` + ``relaxation_plan``),
    so Agent 2's facts need no extra dump of the candidate pool -- the decision
    never looks at the full candidate list.
    """

    feasibility: FeasibilityState
    relaxation_plan: RelaxationPlan


class Agent2Decision(BaseModel):
    """Agent 2's structured decision, grounded in the feasibility facts."""

    decision: DecisionType
    rationale: str = ""
    # PREFER colours Agent 2 is permitted to surrender (unsatisfied_soft).
    relaxed_prefers: list[str] = Field(default_factory=list)
    # source_text of MUST targets with no candidate at any level
    # (unsatisfied_hard): the reported wardrobe gap.
    unmet_must: list[str] = Field(default_factory=list)
    # source_text of MUST targets that are exact-unmet but relaxable -- settling
    # for them means a MUST-level relaxation, which needs user confirmation.
    relaxed_must: list[str] = Field(default_factory=list)
    # Why a clarification is required (ASK_USER only).
    clarification_reason: str = ""


def _unmet_prefers(plan: RelaxationPlan) -> list[str]:
    """Every PREFER colour reported unmet across the relaxation options."""
    seen: list[str] = []
    for option in plan.options:
        for color in option.unmet_prefer_colors:
            if color not in seen:
                seen.append(color)
    return seen


def _relaxed_must_sources(plan: RelaxationPlan) -> list[str]:
    """MUST changes that need a MUST-level relaxation to gain candidates.

    ``minimal_level >= 2`` means the cheapest satisfiable step drops a MUST
    colour (level 2) or broadens the type to its slot (level 3) -- level 0/1
    stay exact / PREFER-only and are never MUST relaxations.
    """
    sources: list[str] = []
    for option in plan.options:
        if option.minimal_level is not None and option.minimal_level >= 2:
            sources.append(option.source_text or option.change_id)
    return sources


def derive_decision(facts: Agent2DecisionFacts) -> Agent2Decision:
    """Classify the decomposed feasibility facts into one of the five decisions."""
    if facts.feasibility is FeasibilityState.NEEDS_CLARIFICATION:
        return Agent2Decision(
            decision=DecisionType.ASK_USER,
            rationale="意图含未解析的指代，必须先向用户澄清才能组合。",
            clarification_reason="请求中的替换对象缺少具体指向",
        )
    # unsatisfied_hard: no candidate at any relaxation level (including the
    # type-to-slot broadening) is a genuine wardrobe gap -- report it.
    hard_gap = [
        option.source_text or option.change_id
        for option in facts.relaxation_plan.options
        if option.unmet
    ]
    if hard_gap:
        return Agent2Decision(
            decision=DecisionType.WARDROBE_GAP,
            rationale="存在 MUST 目标在任一放宽层级都没有候选，无法满足。",
            unmet_must=hard_gap,
        )
    # A MUST target whose exact condition (colour / type / slot) is unmet but
    # relaxable: dropping it is a MUST-level operation and never automatic, so
    # Agent 2 needs a broader retrieval or user confirmation before settling.
    relaxed_must = _relaxed_must_sources(facts.relaxation_plan)
    if facts.feasibility is FeasibilityState.SATISFIABLE_WITH_RELAXATION or relaxed_must:
        return Agent2Decision(
            decision=DecisionType.RETRIEVE_MORE,
            rationale=(
                "有 MUST 目标的精确条件（颜色/品类等）无法满足；"
                "放宽 MUST 属于硬条件操作，不能自动执行，需要更宽的检索或用户确认。"
            ),
            relaxed_must=relaxed_must,
        )
    # unsatisfied_soft: every MUST is exactly satisfied, only a PREFER colour is
    # unobtainable -- surrender that preference, nothing harder.
    unmet_soft = _unmet_prefers(facts.relaxation_plan)
    if unmet_soft:
        return Agent2Decision(
            decision=DecisionType.RELAX_PREFERENCE,
            rationale=(
                "MUST 目标可精确满足，但以下偏好色不可得；只放宽这些偏好，"
                "不放松任何 MUST/MUST_NOT/LOCK。"
            ),
            relaxed_prefers=unmet_soft,
        )
    return Agent2Decision(
        decision=DecisionType.EXACT_MATCH,
        rationale="候选精确满足所有硬条件与偏好。",
    )


def derive_decision_from_facts(facts: Mapping[str, Any]) -> Agent2Decision | None:
    """Reconstruct the structured decision from Agent 1's dumped facts.

    ``None`` when the facts carry no structured feasibility data (e.g. flexible
    adjust mode or a legacy fact set), in which case Agent 2 runs without a
    decision.  Reads only the ``feasibility_report`` summary (``state`` +
    ``relaxation_plan``) already in the facts -- never the candidate pool -- and
    re-validates them so the decision always agrees with what Agent 2 saw.
    """
    if facts.get("adjustment_mode") != "structured":
        return None
    report = facts.get("feasibility_report")
    state_raw = report.get("state") if report else None
    plan_raw = report.get("relaxation_plan") if report else None
    if not state_raw or not plan_raw:
        return None
    return derive_decision(
        Agent2DecisionFacts(
            feasibility=FeasibilityState(state_raw),
            relaxation_plan=RelaxationPlan.model_validate(plan_raw),
        )
    )
