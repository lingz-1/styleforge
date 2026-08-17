"""Structured contracts for the shared three-agent extension pipeline."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from styleforge.core.decision import Agent2Decision
from styleforge.models.context import KnowledgeEvidence
from styleforge.orchestration.task_router import TaskType


class ExtensionIntentEnrichment(BaseModel):
    """Agent 1 interpretation layered on authoritative retrieved facts."""

    intent_summary: str = ""
    target_style: str = ""
    target_occasion: str = ""
    target_item_terms: list[str] = Field(default_factory=list, max_length=12)
    optional_context: list[str] = Field(default_factory=list, max_length=8)


class Agent1TaskOutput(BaseModel):
    """Agent 1 output: interpreted intent plus retrieved, grounded facts."""

    task_type: TaskType
    intent_summary: str
    resolved_target: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    facts: dict[str, Any] = Field(default_factory=dict)
    evidence: list[KnowledgeEvidence] = Field(default_factory=list)
    candidate_item_ids: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_question: str = ""


class ExtensionCompositionNarrative(BaseModel):
    """LLM narrative that may explain, but never replace, tool-grounded data."""

    summary: str = ""
    recommendations: list[str] = Field(default_factory=list, max_length=12)


class Agent2TaskOutput(BaseModel):
    """Agent 2 output: task-specific draft generated from Agent 1 facts."""

    task_type: TaskType
    status: Literal["completed", "infeasible", "needs_clarification"]
    summary: str = ""
    result: dict[str, Any]
    used_item_ids: list[str] = Field(default_factory=list)
    evidence_source_ids: list[str] = Field(default_factory=list)
    # PR4A: deterministic decision (EXACT_MATCH / RELAX_PREFERENCE / RETRIEVE_MORE
    # / ASK_USER / WARDROBE_GAP) derived from the CandidatePool + relaxation plan.
    # Set by the composer from the feasibility facts; the LLM never fills it (it
    # is excluded from the LLM-facing JSON schema).
    decision: Agent2Decision | None = None


class ExtensionReview(BaseModel):
    """Agent 3 semantic review layered on top of hard validation."""

    approved: bool = True
    grounded: bool = True
    summary: str = ""
    issues: list[str] = Field(default_factory=list, max_length=12)


class Agent3TaskOutput(BaseModel):
    """Agent 3 output: validated final task-specific result."""

    task_type: TaskType
    status: Literal["completed", "infeasible", "needs_clarification"]
    approved: bool
    grounded: bool
    summary: str = ""
    checks: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    result: dict[str, Any]
