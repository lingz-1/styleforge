"""Shared semantic layer: natural-language request -> structured RequestSpec.

Architecture note (S1 of the semantic rework):
  This module is the single source of truth for *how natural language maps to
  structured intent* (entity mentions, operators, constraint strength).  It
  reuses the low-level lexical helpers already living in ``request_parser``
  (color/subtype negation, occasion/slot inference) rather than re-implementing
  them, and generalizes them into an operation + change-set model that both the
  recommend and modify task families can consume.

  Layer boundaries:
    * ``interpret_request``   - LLM-free, deterministic semantic parsing.
    * ``RequestSpec``         - the parsed, provenance-carrying intent.
    * ``to_taskspec``         - projection back to the legacy recommend
                                ``TaskSpec`` so the recommendation mainline is
                                untouched.

  Per the rework rules: NO case-specific branching here ("if 帽子 ..."), only
  generic operator/entity/strength vocabulary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from styleforge.core.garment_attributes import SUBTYPE_RULES
from styleforge.core.request_parser import (
    COLOR_ALIASES,
    OCCASION_HARD_CONSTRAINTS,
    SUBTYPE_ALIASES,
    _find_colors,
    _find_occasion,
    _find_required_item_types,
    _find_required_slots,
    _find_subtype_constraints,
    _find_target_audiences,
)
from styleforge.core.schemas import TaskSpec


class Operation(str, Enum):
    RECOMMEND = "RECOMMEND"
    MODIFY = "MODIFY"
    GAP = "GAP"
    STYLE_ADVICE = "STYLE_ADVICE"
    ITEM_ADVICE = "ITEM_ADVICE"


class ConstraintStrength(str, Enum):
    MUST = "MUST"
    MUST_NOT = "MUST_NOT"
    PREFER = "PREFER"
    OPTIONAL = "OPTIONAL"


class ChangeAction(str, Enum):
    ADD = "ADD"
    REMOVE = "REMOVE"
    REPLACE = "REPLACE"
    ADD_OR_REPLACE = "ADD_OR_REPLACE"
    KEEP = "KEEP"
    LOCK = "LOCK"


class EntityRef(BaseModel):
    """A canonical catalog reference: slot and/or item_type and/or subtype."""

    slot: str | None = None
    item_type: str | None = None
    subtype: str | None = None


class AttributePref(BaseModel):
    attribute: str = "color_family"
    value: str
    strength: ConstraintStrength = ConstraintStrength.PREFER


class Change(BaseModel):
    """One user-intended action against a target entity.

    ``action`` (what to do) and ``strength`` (how hard) are independent
    dimensions; e.g. "鞋换成运动鞋" is REPLACE+MUST while "鞋最好换成运动鞋"
    is REPLACE+PREFER.  ``subject`` is the entity the action operates *on* for
    the 把/将 construction ("把这件大衣换成西装" -> REPLACE target=西装,
    subject=大衣): the current item being swapped out / removed.  It anchors the
    change to the session's current outfit instead of leaving the anaphor
    unresolved.  None when no explicit subject is spoken ("换成西装").
    """

    action: ChangeAction
    target: EntityRef
    strength: ConstraintStrength
    subject: EntityRef | None = None
    preferences: list[AttributePref] = Field(default_factory=list)
    source_text: str = ""
    source_span: tuple[int, int] | None = None
    parser_source: str = "deterministic"
    confidence: float = 1.0


class GlobalConstraint(BaseModel):
    """An include/exclude constraint not tied to a modify action (recommend)."""

    strength: ConstraintStrength
    target: EntityRef
    preferences: list[AttributePref] = Field(default_factory=list)
    source_text: str = ""
    source_span: tuple[int, int] | None = None
    parser_source: str = "deterministic"
    confidence: float = 1.0


class Lock(BaseModel):
    target: EntityRef
    source_text: str = ""


class RequestSpec(BaseModel):
    operation: Operation
    changes: list[Change] = Field(default_factory=list)
    constraints: list[GlobalConstraint] = Field(default_factory=list)
    locks: list[Lock] = Field(default_factory=list)
    preferred_colors: list[str] = Field(default_factory=list)
    excluded_colors: list[str] = Field(default_factory=list)
    # color -> MUST/PREFER (default PREFER when only "想要黑"); MUST_NOT lives
    # in excluded_colors.
    color_strength: dict[str, ConstraintStrength] = Field(default_factory=dict)
    unresolved_fields: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Entity vocabulary (canonical item_type-level aliases).  Subtype-level aliases
# come from request_parser.SUBTYPE_ALIASES.  This table is the single home for
# natural-language mentions -> ALLOWED_ITEM_TYPES mapping.
# ---------------------------------------------------------------------------

ENTITY_ITEM_TYPE_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("top", ("上衣", "top", "shirt", "blouse", "tee")),
    ("pants", ("裤子", "长裤", "pants", "trousers")),
    ("shorts", ("短裤", "shorts")),
    ("skirt", ("裙子", "半身裙", "skirt")),
    ("dress", ("连衣裙", "dress")),
    ("outwear", ("外套", "大衣", "风衣", "夹克", "coat", "jacket", "blazer")),
    ("shoes", ("鞋", "靴", "靴子", "shoes", "boots")),
    ("bag", ("包", "包包", "手袋", "bag", "handbag")),
    ("hats", ("帽子", "帽", "hat", "cap", "beanie", "fedora")),
    (
        "hairwear",
        ("发夹", "发卡", "发饰", "发圈", "发带", "头箍", "发箍", "hair clip", "hairwear", "headband"),
    ),
    ("earrings", ("耳环", "耳钉", "耳坠", "earrings")),
    ("necklace", ("项链", "颈链", "necklace", "choker")),
    ("bracelet", ("手链", "手镯", "bracelet", "bangle")),
    ("rings", ("戒指", "指环", "rings")),
    ("eyewear", ("墨镜", "太阳镜", "眼镜", "sunglasses", "eyeglasses")),
    ("belts", ("腰带", "皮带", "belt")),
    ("gloves", ("手套", "gloves")),
    ("watches", ("手表", "腕表", "watch")),
    ("brooch", ("胸针", "brooch")),
    ("jewellery", ("首饰", "珠宝", "jewellery", "jewelry")),
    ("legwear", ("丝袜", "裤袜", "legwear", "stockings")),
    ("swimwear", ("泳装", "泳衣", "比基尼", "swimsuit", "bikini")),
    ("underwear", ("内衣", "内裤", "文胸", "underwear", "bra")),
    ("sleepwear", ("睡衣", "睡袍", "pajama", "sleepwear")),
    ("jumpsuit", ("连体裤", "连衣裤", "jumpsuit")),
    ("suit", ("西装", "套装", "suit")),
)


# Operator vocabulary: (word, action, default_strength).  Longer words first so
# "换成"/"换掉" win over the bare "换" and "不要" wins over the "要" it contains.
_OPERATOR_WORDS: tuple[tuple[str, ChangeAction, ConstraintStrength], ...] = (
    ("替换成", ChangeAction.REPLACE, ConstraintStrength.MUST),
    ("换成", ChangeAction.REPLACE, ConstraintStrength.MUST),
    ("改成", ChangeAction.REPLACE, ConstraintStrength.MUST),
    ("换掉", ChangeAction.REMOVE, ConstraintStrength.MUST_NOT),
    ("摘掉", ChangeAction.REMOVE, ConstraintStrength.MUST_NOT),
    ("去掉", ChangeAction.REMOVE, ConstraintStrength.MUST_NOT),
    ("撤掉", ChangeAction.REMOVE, ConstraintStrength.MUST_NOT),
    ("不要", ChangeAction.REMOVE, ConstraintStrength.MUST_NOT),
    ("不想", ChangeAction.REMOVE, ConstraintStrength.MUST_NOT),
    ("不喜欢", ChangeAction.REMOVE, ConstraintStrength.MUST_NOT),
    ("不穿", ChangeAction.REMOVE, ConstraintStrength.MUST_NOT),
    ("避免", ChangeAction.REMOVE, ConstraintStrength.MUST_NOT),
    ("排除", ChangeAction.REMOVE, ConstraintStrength.MUST_NOT),
    ("禁用", ChangeAction.REMOVE, ConstraintStrength.MUST_NOT),
    ("别戴", ChangeAction.REMOVE, ConstraintStrength.MUST_NOT),
    ("别穿", ChangeAction.REMOVE, ConstraintStrength.MUST_NOT),
    ("别", ChangeAction.REMOVE, ConstraintStrength.MUST_NOT),
    ("加", ChangeAction.ADD, ConstraintStrength.MUST),
    ("添", ChangeAction.ADD, ConstraintStrength.MUST),
    ("补", ChangeAction.ADD, ConstraintStrength.MUST),
    ("想要", ChangeAction.ADD_OR_REPLACE, ConstraintStrength.MUST),
    ("想", ChangeAction.ADD_OR_REPLACE, ConstraintStrength.MUST),
    ("要", ChangeAction.ADD_OR_REPLACE, ConstraintStrength.MUST),
    ("换", ChangeAction.REPLACE, ConstraintStrength.MUST),
    ("保留", ChangeAction.LOCK, ConstraintStrength.MUST),
    ("别动", ChangeAction.LOCK, ConstraintStrength.MUST),
    ("保持", ChangeAction.LOCK, ConstraintStrength.MUST),
    ("留着", ChangeAction.LOCK, ConstraintStrength.MUST),
    ("锁住", ChangeAction.LOCK, ConstraintStrength.MUST),
    ("不动", ChangeAction.LOCK, ConstraintStrength.MUST),
)

_OPERATOR_WORDS_BY_LENGTH: tuple[tuple[str, ChangeAction, ConstraintStrength], ...] = tuple(
    sorted(_OPERATOR_WORDS, key=lambda t: len(t[0]), reverse=True)
)

# The 把/将 construction marks the entity an action operates on: in
# "把这件大衣换成西装" the 大衣 is the subject of the REPLACE, not a new
# include recommendation.  Capturing it lets CandidateService anchor the change
# to the session's current outfit instead of leaving the anaphor unresolved.
_SUBJECT_MARKERS = ("把", "将")
# Max quantifier/adjective chars between the marker and the entity ("这件深灰
# 色" ~= 5).  Beyond this the marker is too far to govern the mention.
_SUBJECT_GAP_LIMIT = 6


def _collect_subject_spans(
    text: str, mentions: list[EntityMention]
) -> dict[tuple[int, int], EntityRef]:
    """Map subject spans (the thing an action acts *on*) to their entity.

    Two constructions put an entity before its operator, making it the subject
    rather than a bare include recommendation:

    * 把/将: "把这件大衣换成西装" -> the 大衣 being swapped out.
    * Entity-before-REPLACE: "外套换件西装" -> the current 外套 replaced by a
      suit.  Restricted to REPLACE operators so "X，不要红色" (a colour
      negation, not an entity removal) never mis-marks X.

    A subject span is consumed by its governing REPLACE/REMOVE change and, in a
    session, anchors the change to the current outfit so the anaphor can be
    resolved.  Single characters ("把它换掉") are skipped: that is a genuinely
    unresolved anaphor.
    """
    subjects: dict[tuple[int, int], EntityRef] = {}
    for mention in mentions:
        if len(mention.source) < 2:
            continue
        sent_start = _sent_start(text, mention.start)
        before = text[sent_start:mention.start]
        for marker in _SUBJECT_MARKERS:
            mpos = before.rfind(marker)
            if mpos < 0:
                continue
            if len(before) - mpos - len(marker) > _SUBJECT_GAP_LIMIT:
                continue
            subjects[(mention.start, mention.end)] = _mention_entity_ref(mention)
            break
        else:
            sent_end = len(text)
            for match in _SENTENCE_BOUNDS.finditer(text, mention.end):
                sent_end = match.start()
                break
            after = text[mention.end:sent_end]
            if any(
                0 <= after.find(word) <= 3 and action is ChangeAction.REPLACE
                for word, action, _ in _OPERATOR_WORDS_BY_LENGTH
            ):
                subjects[(mention.start, mention.end)] = _mention_entity_ref(mention)
    return subjects


def _subject_for_change(
    subjects: dict[tuple[int, int], EntityRef], op_pos: int
) -> EntityRef | None:
    """The subject governing the change whose operator starts at ``op_pos``.

    The nearest preceding 把/将 object wins ("把这件大衣换成西装" -> op_pos of
    换成 = 5 -> subject span (3, 5) -> 大衣).  Returns None when no 把/将
    subject precedes this operator.
    """
    candidates = [(end, ref) for (_, end), ref in subjects.items() if end <= op_pos]
    if not candidates:
        return None
    return max(candidates, key=lambda pair: pair[0])[1]


_STRENGTH_MUST_WORDS = ("必须", "一定", "只能", "只允许", "非要", "就得")
_STRENGTH_PREFER_WORDS = ("最好", "尽量", "希望", "倾向于", "比较想", "有点想")
_STRENGTH_OPTIONAL_WORDS = ("没有也行", "可有可无", "不强求", "无所谓")

# Verbs between an operator and its entity invalidate the operator ("我要推荐
# 一个粉色包包" -> "要" must not govern "包包").
_BLOCKING_WORDS = ("推荐", "介绍", "看看", "搭配", "找", "穿起来", "放")

_SENTENCE_BOUNDS = re.compile(r"[，,。；;！!？?\n]")


def _sent_start(text: str, position: int) -> int:
    """Index just after the last sentence boundary before ``position``."""
    last = 0
    for match in _SENTENCE_BOUNDS.finditer(text, 0, position):
        last = match.end()
    return last


def _normalize_request(request: str) -> str:
    return re.sub(r"\s+", " ", request.strip().lower())


# ---------------------------------------------------------------------------
# Mention collection: natural-language entity -> EntityRef with provenance.
# ---------------------------------------------------------------------------


@dataclass
class EntityMention:
    start: int
    end: int
    source: str
    item_type: str | None = None
    subtype: str | None = None
    slot: str | None = None


def _collect_mentions(text: str) -> list[EntityMention]:
    matches: list[EntityMention] = []
    for item_type, aliases in ENTITY_ITEM_TYPE_ALIASES:
        for alias in aliases:
            start = text.find(alias)
            while start >= 0:
                matches.append(EntityMention(start, start + len(alias), alias, item_type=item_type))
                start = text.find(alias, start + len(alias))
    for slot, subtype, aliases in SUBTYPE_ALIASES:
        for alias in aliases:
            start = text.find(alias.lower())
            while start >= 0:
                matches.append(
                    EntityMention(start, start + len(alias), alias, subtype=subtype, slot=slot)
                )
                start = text.find(alias.lower(), start + len(alias))
    return _dedupe_overlaps(matches)


def _dedupe_overlaps(mentions: list[EntityMention]) -> list[EntityMention]:
    """Merge/keep mentions across overlapping spans.

    * Same span from different sources ("发夹" is both item_type=hairwear in
      ENTITY_ITEM_TYPE_ALIASES and subtype=hair_clip in SUBTYPE_ALIASES) merge
      into one mention carrying both, so CandidateService can resolve by
      item_type AND keep the subtype hint.
    * Different-length overlaps keep the longest (subtype "运动鞋" beats the
      bare "鞋"; "连衣裙" beats "裙").
    """
    merged: list[EntityMention] = []
    for mention in sorted(mentions, key=lambda m: (m.start, -(m.end - m.start))):
        same_span = [
            x for x in merged
            if x.start == mention.start and x.end == mention.end
        ]
        if same_span:
            target = same_span[0]
            target.item_type = target.item_type or mention.item_type
            target.subtype = target.subtype or mention.subtype
            target.slot = target.slot or mention.slot
            if len(mention.source) > len(target.source):
                target.source = mention.source
            continue
        overlap = [
            x for x in merged
            if mention.start < x.end and mention.end > x.start
        ]
        if not overlap:
            merged.append(mention)
            continue
        if (mention.end - mention.start) > (overlap[0].end - overlap[0].start):
            merged.remove(overlap[0])
            merged.append(mention)
    return sorted(merged, key=lambda m: m.start)


def _mention_entity_ref(mention: EntityMention) -> EntityRef:
    return EntityRef(
        slot=mention.slot,
        item_type=mention.item_type,
        subtype=mention.subtype,
    )


def _detect_operator(text: str, mention_start: int) -> tuple[ChangeAction, ConstraintStrength, str, int]:
    """Return (action, default_strength, operator_word, operator_position) for the
    operator governing the mention at ``mention_start``.

    Longest-word-first so "不要" beats the "要" it contains; the gap between the
    operator and the entity must not contain a blocking verb and must be short
    enough to be a modifier chain (quantifier/strength/color).
    """
    sent_start = _sent_start(text, mention_start)
    window = text[sent_start:mention_start]
    for word, action, strength in _OPERATOR_WORDS_BY_LENGTH:
        pos = window.rfind(word)
        if pos < 0:
            continue
        op_pos = sent_start + pos
        gap = text[op_pos + len(word):mention_start]
        if any(block in gap for block in _BLOCKING_WORDS):
            continue
        if len(gap) > 6:
            continue
        return (action, strength, word, op_pos)
    # No (valid) operator: a bare positive mention (e.g. "粉色连衣裙").
    return (ChangeAction.ADD_OR_REPLACE, ConstraintStrength.PREFER, "", mention_start)


# Post-positioned operators (entity before the operator): "包别动" / "帽子留
# 着" / "这件去掉".  Only consulted when no pre-positioned operator exists.
_POST_OPERATOR_WORDS: tuple[tuple[str, ChangeAction, ConstraintStrength], ...] = (
    ("别动", ChangeAction.LOCK, ConstraintStrength.MUST),
    ("不动", ChangeAction.LOCK, ConstraintStrength.MUST),
    ("留着", ChangeAction.LOCK, ConstraintStrength.MUST),
    ("保留", ChangeAction.LOCK, ConstraintStrength.MUST),
    ("锁住", ChangeAction.LOCK, ConstraintStrength.MUST),
    ("去掉", ChangeAction.REMOVE, ConstraintStrength.MUST_NOT),
    ("摘掉", ChangeAction.REMOVE, ConstraintStrength.MUST_NOT),
    ("撤掉", ChangeAction.REMOVE, ConstraintStrength.MUST_NOT),
    ("拿掉", ChangeAction.REMOVE, ConstraintStrength.MUST_NOT),
    ("换掉", ChangeAction.REMOVE, ConstraintStrength.MUST_NOT),
)


def _detect_post_operator(
    text: str, mention_end: int
) -> tuple[ChangeAction, ConstraintStrength, str, int] | None:
    """Detect a post-positioned operator right after ``mention_end`` within the
    same sentence (e.g. the "别动" in "包别动")."""
    sent_end = len(text)
    for match in _SENTENCE_BOUNDS.finditer(text, mention_end):
        sent_end = match.start()
        break
    after = text[mention_end:sent_end]
    for word, action, strength in _POST_OPERATOR_WORDS:
        pos = after.find(word)
        if pos >= 0 and pos <= 3:
            return (action, strength, word, mention_end + pos)
    return None


def _detect_strength(text: str, op_pos: int, mention_start: int) -> ConstraintStrength | None:
    window = text[op_pos:mention_start]
    for word in _STRENGTH_OPTIONAL_WORDS:
        if word in window:
            return ConstraintStrength.OPTIONAL
    for word in _STRENGTH_PREFER_WORDS:
        if word in window:
            return ConstraintStrength.PREFER
    for word in _STRENGTH_MUST_WORDS:
        if word in window:
            return ConstraintStrength.MUST
    return None


def _detect_color_prefs(text: str, mention_start: int) -> list[AttributePref]:
    """Colors appearing before a mention (same sentence) become per-entity
    preferences (strength PREFER unless a MUST word appears)."""
    sent_start = _sent_start(text, mention_start)
    before = text[sent_start:mention_start]
    prefs: list[AttributePref] = []
    for canonical, aliases in COLOR_ALIASES.items():
        positions = [before.rfind(alias) for alias in aliases if alias in before]
        if not positions:
            continue
        pos = max(positions)
        strength = ConstraintStrength.MUST if any(
            w in before[max(0, pos - 8):pos] for w in _STRENGTH_MUST_WORDS
        ) else ConstraintStrength.PREFER
        prefs.append(AttributePref(attribute="color_family", value=canonical, strength=strength))
    return prefs


def _parse_semantics(text: str) -> tuple[list[Change], list[GlobalConstraint], list[Lock], list[str]]:
    changes: list[Change] = []
    constraints: list[GlobalConstraint] = []
    locks: list[Lock] = []
    unresolved: list[str] = []
    mentions = list(_collect_mentions(text))
    # Entities that are the object of a 把/将 construction ("把这件大衣换成西
    # 装" -> 大衣) are the *subject* of a change, not a new include
    # recommendation; they are consumed by the governing REPLACE/REMOVE.
    subjects = _collect_subject_spans(text, mentions)
    for mention in mentions:
        single_han = len(mention.source) == 1 and ord(mention.source[0]) > 0x4E00
        action, default_strength, op_word, op_pos = _detect_operator(text, mention.start)
        # A post-positioned operator (entity first: "包别动") applies only when
        # no pre-positioned operator governs this mention.
        post = None
        if not op_word:
            post = _detect_post_operator(text, mention.end)
            if post is not None:
                action, default_strength, op_word, op_pos = post
        if single_han and not op_word:
            continue
        if not op_word:
            if (mention.start, mention.end) in subjects:
                # The 把/将 object is consumed by a later REPLACE/REMOVE; it is
                # the thing being swapped out, not a positive recommendation.
                continue
            # Bare positive mention -> recommend include constraint.
            strength = (
                _detect_strength(text, _sent_start(text, mention.start), mention.start)
                or default_strength
            )
            constraints.append(
                GlobalConstraint(
                    strength=strength,
                    target=_mention_entity_ref(mention),
                    preferences=_detect_color_prefs(text, mention.start),
                    source_text=mention.source,
                    source_span=(mention.start, mention.end),
                )
            )
            continue
        if action is ChangeAction.LOCK:
            locks.append(Lock(target=_mention_entity_ref(mention), source_text=mention.source))
            continue
        strength = _detect_strength(text, op_pos, mention.start) or default_strength
        subject = _subject_for_change(subjects, op_pos)
        changes.append(
            Change(
                action=action,
                target=_mention_entity_ref(mention),
                strength=strength,
                subject=subject if action in (ChangeAction.REPLACE, ChangeAction.REMOVE) else None,
                preferences=_detect_color_prefs(text, mention.start),
                source_text=mention.source,
                source_span=(mention.start, mention.end),
            )
        )
    if re.search(r"(那|这|它)(个|件|双|只|条|顶|对|枚)", text):
        unresolved.append("anaphoric_reference")
    return changes, constraints, locks, unresolved


def _color_strength_map(text: str, preferred_colors: tuple[str, ...]) -> dict[str, ConstraintStrength]:
    """Per-color strength: MUST when a MUST word precedes the mention, else PREFER."""
    result: dict[str, ConstraintStrength] = {}
    for color in preferred_colors:
        aliases = COLOR_ALIASES[color]
        positions = [text.find(alias) for alias in aliases if text.find(alias) >= 0]
        if not positions:
            result[color] = ConstraintStrength.PREFER
            continue
        pos = min(positions)
        context = text[max(0, pos - 10):pos]
        result[color] = (
            ConstraintStrength.MUST
            if any(word in context for word in _STRENGTH_MUST_WORDS)
            else ConstraintStrength.PREFER
        )
    return result


def _detect_operation(text: str, changes: list[Change], unresolved: list[str]) -> Operation:
    if changes or "anaphoric_reference" in unresolved:
        return Operation.MODIFY
    if any(word in text for word in ("缺", "缺少", "还缺", "补点什么", "补充")):
        return Operation.GAP
    if "怎么穿" in text or "如何搭" in text or "搭配建议" in text:
        return Operation.STYLE_ADVICE
    return Operation.RECOMMEND


def interpret_request(request: str) -> RequestSpec:
    """Parse any styling request into a structured, provenance-carrying spec.

    Deterministic first layer: entity mentions, operators, strengths and colors.
    The returned spec carries ``metadata.normalized_request`` for downstream
    re-use (TaskSpec projection).  Unresolved anaphora is recorded, never
    guessed.  No LLM is used here; an optional LLM refinement may *add* to
    ``unresolved_fields`` but must not override these explicit constraints.
    """
    normalized = _normalize_request(request)
    changes, constraints, locks, unresolved = _parse_semantics(normalized)
    preferred_colors, excluded_colors = _find_colors(normalized)
    return RequestSpec(
        operation=_detect_operation(normalized, changes, unresolved),
        changes=changes,
        constraints=constraints,
        locks=locks,
        preferred_colors=list(preferred_colors),
        excluded_colors=list(excluded_colors),
        color_strength=_color_strength_map(normalized, preferred_colors),
        unresolved_fields=unresolved,
        metadata={"normalized_request": normalized},
    )


# ---------------------------------------------------------------------------
# Projection: RequestSpec -> legacy TaskSpec (recommend mainline).
# ---------------------------------------------------------------------------


def to_taskspec(spec: RequestSpec, user_id: str, max_results: int = 3) -> TaskSpec:
    """Rebuild the recommend ``TaskSpec`` from a parsed RequestSpec.

    Kept behaviourally identical to the historical ``parse_request`` body so the
    recommendation mainline (planner -> task_workflow -> chat) sees no change:
    occasion-injected hard constraints, subtype->item_type inference and slot
    derivation all still run here, reading colors from the spec.
    """
    normalized = spec.metadata.get("normalized_request", "")
    required_subtypes, excluded_subtypes = _find_subtype_constraints(normalized)
    user_required_subtypes = dict(required_subtypes)
    required_item_types = _find_required_item_types(normalized)
    occasion = _find_occasion(normalized)
    for slot, item_types in OCCASION_HARD_CONSTRAINTS.get(
        occasion, {}
    ).get("required_item_types_by_slot", {}).items():
        existing = required_item_types.get(slot, ())
        required_item_types[slot] = tuple(dict.fromkeys((*existing, *item_types)))
    for slot, subtypes in OCCASION_HARD_CONSTRAINTS.get(
        occasion, {}
    ).get("required_subtypes_by_slot", {}).items():
        existing = required_subtypes.get(slot, ())
        required_subtypes[slot] = tuple(dict.fromkeys((*existing, *subtypes)))
    for slot, subtypes in OCCASION_HARD_CONSTRAINTS.get(
        occasion, {}
    ).get("excluded_subtypes_by_slot", {}).items():
        existing = excluded_subtypes.get(slot, ())
        excluded_subtypes[slot] = tuple(dict.fromkeys((*existing, *subtypes)))
    for slot, subtypes in required_subtypes.items():
        inferred_types = tuple(
            dict.fromkeys(
                item_type
                for subtype in subtypes
                for item_type in SUBTYPE_RULES[subtype].item_types
            )
        )
        if inferred_types:
            existing = required_item_types.get(slot, ())
            required_item_types[slot] = tuple(dict.fromkeys((*existing, *inferred_types)))
    required_slots = list(_find_required_slots(normalized))
    for slot in user_required_subtypes:
        if slot not in required_slots:
            required_slots.append(slot)
    return TaskSpec(
        user_id=user_id,
        occasion=occasion,
        target_audiences=_find_target_audiences(normalized),
        required_slots=tuple(required_slots),
        required_item_types_by_slot=required_item_types,
        required_subtypes_by_slot=required_subtypes,
        excluded_subtypes_by_slot=excluded_subtypes,
        excluded_colors=tuple(spec.excluded_colors),
        excluded_name_keywords=OCCASION_HARD_CONSTRAINTS.get(
            occasion, {}
        ).get("excluded_name_keywords", ()),
        preferred_colors=tuple(spec.preferred_colors),
        max_results=max_results,
    )
