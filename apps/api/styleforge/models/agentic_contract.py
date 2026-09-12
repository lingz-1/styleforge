"""Shared semantic-agent and deterministic-environment contracts.

The current Multi-Agent Harness imports these models directly. See
``docs/AGENTIC_CONTRACT.md`` for the boundary rationale and its evolution.

The contract fixes four facts:

1. What the frontend may send (``InteractionContext``) — and nothing else.
   No ``REPLACE / ADJUST / FLEXIBLE``, no task-typed intents.
2. What the program supplies to the Agent (``EnvironmentFacts``) — grounded
   facts only, never an interpretation. The Agent reads it, never writes it.
3. What the Agent produces (``UserIntent`` + tool calls) — the Agent owns
   semantics, decisions, and replanning; the program owns execution and
   physical legality.
4. What the program refuses to interpret. There is no hard/soft constraint
   judgement, no ``subject / target / required_slot / relaxation_level``, and
   no long-lived ``confirmed_outfit``.

Three-way boundary (the core of this revision):

    Semantic Agent             → understand the user, decide, act
    Deterministic Environment  → facts, execution, structural legality
    Semantic Reviewer / Critic → did the result violate the user's intent?

The program never judges *what the user wants* ("不要红色", "上衣别动" are
semantic, read by the Agent and the Reviewer). The Agent never decides whether
the database and the physical structure are legal (that is check_environment's
job).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ── InteractionContext ─────────────────────────────────────────────
# Frontend grounding. Program-filled, never guessed by the Agent.

class InteractionContext(BaseModel):
    """What the UI knows about the user's current focus.

    Clicking an outfit card sets ``active_outfit_id``; clicking a single
    item sets ``selected_item_id``. Both are *disambiguation aids only* —
    never preconditions and never an implied operation. Clicking an item
    does NOT produce a ``REPLACE``; it only grounds the next user message.

    Resolution order when a message arrives:
      1. explicit context above (strongest),
      2. Agent-inferred grounding against ``visible_outfits`` (e.g. "第二套
         鞋子换乐福鞋"),
      3. ``ask_user`` when neither resolves and disambiguation matters.
    """
    active_outfit_id: str | None = None
    selected_item_id: str | None = None


# ── Snapshots (ground truth shapes, program-produced) ──────────────

class ItemSnapshot(BaseModel):
    item_id: str
    name: str = ""
    item_type: str = ""
    color: str = ""
    features: list[str] = Field(default_factory=list)
    structure: GarmentStructure | None = None  # forward ref; see §8 ontology


class OutfitSnapshot(BaseModel):
    outfit_id: str
    item_ids: list[str] = Field(default_factory=list)
    items: list[ItemSnapshot] = Field(default_factory=list)
    score: float | None = None
    reasoning: str = ""


# ── EnvironmentFacts ───────────────────────────────────────────────
# Ground truth the program supplies. The Agent reads it; it never writes it.

class EnvironmentFacts(BaseModel):
    """Everything the program knows and the Agent may rely on.

    Deliberately absent: any hard/soft classification, any
    ``replaced_item_ids / locked_item_ids`` as Agent-writable fields, any
    ``relaxation_level``, and any predicted result shape the Agent must
    reproduce. Physical legality lives in ``check_environment``; intent
    fidelity lives in ``review_outfit``.
    """
    interaction: InteractionContext = Field(default_factory=InteractionContext)
    visible_outfits: list[OutfitSnapshot] = Field(default_factory=list)
    active_outfit: OutfitSnapshot | None = None
    selected_item: ItemSnapshot | None = None
    wardrobe_summary: dict[str, Any] = Field(default_factory=dict)
    # A bounded, request-scoped shortlist prepared by the deterministic
    # retrieval layer. It contains facts, not an interpretation of the user's
    # intent; the Stylist still decides whether and how to use each item.
    request_candidates: list[ItemSnapshot] = Field(default_factory=list)
    weather: dict[str, Any] | None = None
    memory_profile: dict[str, Any] = Field(default_factory=dict)


# ── UserIntent ─────────────────────────────────────────────────────
# The Agent's interpretation. The Agent owns this, entirely.

class UserIntent(BaseModel):
    """The Agent's reading of the user message.

    ``goal`` stays natural language. ``requirements`` are natural-language
    descriptions of what the user asked for ("上衣保持", "鞋倾向深色").

    There is deliberately NO hard/soft strength on requirements. Whether an
    item of a requirement is negotiable, how much, and whether the user should
    be asked, are all understood by the Agent from the utterance itself — the
    program never classifies them. ``goal`` and ``requirements`` exist to (a)
    steer the Agent's own actions and (b) give ``review_outfit`` the intent it
    must check the result against.
    """
    message: str
    goal: str = ""
    requirements: list[str] = Field(default_factory=list)


# ── Structure ontology ─────────────────────────────────────────────
# Physical structure only. The program validates "can these garments coexist",
# never "does this look good" (that is Agent + Skill/RAG + Reviewer).

class BodyRegion(str, Enum):
    upper_body = "upper_body"
    lower_body = "lower_body"
    full_body = "full_body"
    feet = "feet"
    accessory = "accessory"


class GarmentLayer(str, Enum):
    base = "base"
    mid = "mid"
    outer = "outer"


class GarmentStructure(BaseModel):
    """Physical definition of one garment type.

    ``allowed_layers`` is the set of layers this garment may occupy. A shirt
    allowed on ``base`` and ``mid`` leaves the exact layer to the Agent's
    placement decision. ``occupancy`` is the body regions physically taken
    (a dress occupies upper + lower; a t-shirt only upper).

    Conflict is judged at ``(occupied_region, layer)`` granularity, NOT at
    ``region`` alone — otherwise a dress (upper/lower · base) plus a coat
    (upper · outer) would be falsely flagged. ``exclusive`` is structural
    cardinality: shoes / trousers / skirts are exclusive at their
    ``(region, layer)``, while accessories (necklace, earrings, watch, bag)
    are ``exclusive=False`` so several may coexist on the same
    ``(accessory, *)`` cell. This is physical structure, not style taste.
    """
    allowed_region: BodyRegion
    allowed_layers: list[GarmentLayer] = Field(default_factory=list)
    occupancy: list[BodyRegion] = Field(default_factory=list)
    exclusive: bool = True

    def resolves_occupancy(self) -> list[BodyRegion]:
        return self.occupancy or [self.allowed_region]


class Placement(BaseModel):
    """Where the Agent wants a garment worn. The Agent decides; the program
    only verifies the garment allows it and nothing conflicts.

    Both fields are optional: the region is a *structural fact* the program
    derives from the garment type, and the layer falls back to the garment's
    effective (lowest) layer. A real model may omit either — the environment
    fills the gaps deterministically. A *wrong* explicit value is still
    rejected by ``placement_error``.
    """
    region: BodyRegion | None = None
    layer: GarmentLayer | None = None


# ── Modify (transactional) ─────────────────────────────────────────
# The Agent proposes a plan; the program executes it atomically on a copy of
# the current outfit, validates the *complete resulting state*, then commits
# or rolls back. The Agent never mutates the real outfit directly and never
# returns a result snapshot to be trusted verbatim.

class ModifyOp(BaseModel):
    """One atomic mutation. Only true environment-changing actions remain:
    ``add / remove / replace``. ``lock / keep`` are *user semantics*, not
    environment actions — keeping them would regrow ``locked_item_ids`` /
    ``kept_item_ids`` / ``replaced_item_ids``. Intent fidelity is judged by
    ``review_outfit`` instead.
    """
    action: Literal["add", "remove", "replace"]
    item_id: str | None = None                 # target (remove / replace)
    replacement_item_id: str | None = None     # for replace
    placement: Placement | None = None         # for add / replace (Agent decides the layer)
    reason: str = ""


class ModifyPlan(BaseModel):
    ops: list[ModifyOp] = Field(default_factory=list)
    reasoning: str = ""


class ModifyOutcome(BaseModel):
    """``modify_outfit`` result: a *candidate*, never a committed state.

    The orchestrator holds this in a draft. The commit gate is NOT
    ``check_environment`` alone — it is ``check_environment PASS AND
    review_outfit PASS``. Until then nothing touches the real session state:

        current outfit → clone/draft → apply plan → check_environment
        → review_outfit → PASS → commit  |  FAIL → discard → replan

    On any failure the draft is discarded and the issues return to the Agent
    as an observation, so a replan never operates on polluted state.
    """
    candidate: OutfitSnapshot
    plan: ModifyPlan


# ── Tools ──────────────────────────────────────────────────────────
# High-level tools (≈7). Each returns facts; none of them make decisions.

class WardrobeSearchResult(BaseModel):
    """``search_wardrobe`` return. An empty result is a fact, not a verdict.

    The Agent decides what an empty result means (retry a near expression,
    query colour variants, offer a soft alternative, or ask the user). The
    program never auto-asks here.
    """
    results: list[ItemSnapshot] = Field(default_factory=list)
    matched: int = 0
    query: str = ""
    retrieval_mode: Literal["keyword", "semantic", "hybrid"] = "keyword"
    semantic_available: bool = False
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class WebSearchHit(BaseModel):
    """One web search result. ``content`` is truncated inside the provider
    (~200 chars) so a long page never bloats the Agent's context."""

    title: str = ""
    url: str = ""
    content: str = ""


class WebSearchResult(BaseModel):
    """``search_web`` return. Never raises.

    ``available=False`` means the search is unconfigured/disabled (graceful
    degradation); a non-empty ``error`` means this call failed (network /
    parse / API). Both are *facts* the Agent observes and works around — the
    loop must never crash because the web is unreachable.
    """

    query: str = ""
    results: list[WebSearchHit] = Field(default_factory=list)
    answer: str = ""  # LLM summary, only when include_answer=True
    error: str | None = None
    available: bool = True


class SkillResult(BaseModel):
    """``load_skill`` return. Never raises.

    A skill is procedural task knowledge (e.g. event-outfit planning) the
    Agent loads when it recognises the task type. ``available=False`` means
    the skill is missing/unconfigured — a fact the Agent works around.
    """

    name: str = ""
    content: str = ""
    error: str | None = None
    available: bool = True


class PlanState(BaseModel):
    """The Agent's *structured* plan for a complex task (not chain-of-thought).

    Recorded in the trace as a decision summary so Stage 3 can audit whether
    the Agent surveyed what it needed before acting. Optional: simple requests
    act directly and emit no plan_state.
    """

    objective: str = ""
    missing_information: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    completed_steps: list[str] = Field(default_factory=list)
    remaining_steps: list[str] = Field(default_factory=list)


class CheckEnvironmentResult(BaseModel):
    """``check_environment`` — pure deterministic structural legality.

    Checks ONLY: IDs exist and belong to the current user; the ModifyPlan is
    executable against a copy; the resulting state has no region/layer/
    occupancy conflict; the state validates against schema and can persist.

    It NEVER reads "不要红色", "上衣别动", "不要运动" — those are semantic
    and belong to the Agent + ``review_outfit``.
    """
    valid: bool
    issues: list[str] = Field(default_factory=list)  # returned to Agent as replan observation


class AskUser(BaseModel):
    """``ask_user``: the loop suspends for the user's reply."""
    question: str


# ── Semantic Reviewer / Critic ─────────────────────────────────────
# An LLM gate in the loop (not a business tool). Judges intent fidelity and
# quality — the things check_environment must never touch.

class ReviewInput(BaseModel):
    """What the Reviewer sees to judge intent fidelity."""
    user_message: str
    interaction: InteractionContext = Field(default_factory=InteractionContext)
    outfit_before: OutfitSnapshot | None = None
    outfit_after: OutfitSnapshot | None = None
    user_intent: UserIntent | None = None


class ReviewDimensionScores(BaseModel):
    """Five-dimension semantic assessment on the shared 1-10 rubric."""

    model_config = ConfigDict(extra="forbid")

    request_relevance: int = Field(ge=1, le=10)
    request_specificity: int = Field(ge=1, le=10)
    outfit_coordination: int = Field(ge=1, le=10)
    wearability: int = Field(ge=1, le=10)
    freshness: int = Field(ge=1, le=10)


class ReviewResult(BaseModel):
    approved: bool
    issues: list[str] = Field(default_factory=list)
    feedback: str = ""  # for the Agent to replan on
    # Optional for backward compatibility with stored/fake v1 responses. New
    # Critic prompts always request it; callers use an explicit neutral fallback
    # when an older provider response omits the field.
    dimension_scores: ReviewDimensionScores | None = None


# ── SaveOutfit command ─────────────────────────────────────────────
# Saving is a command/event, not a long-lived state.

class SaveOutfitCommand(BaseModel):
    """Persist an outfit into the saved-outfit collection."""
    outfit_id: str
