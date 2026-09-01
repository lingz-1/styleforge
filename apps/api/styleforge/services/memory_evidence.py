"""Behavior evidence extraction (deterministic) + language evidence facade.

Behavior evidence turns a raw ``interaction_event`` into weak/strong
standardized claims without any LLM call. Language evidence is produced by the
LLM (``services.memory_extractor``) and re-exported here so callers have one
import surface.

Layering: the scheme explicitly distinguishes ``不喜欢当前这件`` from
``不喜欢这一品类`` from ``不喜欢这个属性`` from ``长期不喜欢``. A single
behavior event therefore never produces a global category preference:

- ``garment/item=<item_id>``            the exact piece the user acted on (always)
- ``appearance/color=<color>``           weak attribution to the piece's color
- ``garment/category=<category>``       only once N *distinct* items of the
                                        same category show the same polarity
                                        (category induction)

All weak claims carry a ``contextual`` scope with the occasion/formality words
found in the request, so the resolver only activates them when the current
scene matches (the scheme requires ``scope=current_context`` for weak behavior
evidence).

Both produce dicts shaped like ``preference_evidence`` rows, ready for
``memory_aggregator.apply_evidence``.
"""

from __future__ import annotations

from typing import Any

from styleforge.repositories import catalog_repository
from styleforge.repositories.interaction_event_repository import record_event
from styleforge.repositories.preference_evidence_repository import list_evidence
from styleforge.services.memory_aggregator import apply_evidence
from styleforge.services.memory_extractor import extract_memories

# Re-export under the plan's name; implementation lives in memory_extractor.
extract_language_evidence = extract_memories

# Event type -> deterministic claim rule. ``polarity`` may be None for events
# whose sign comes from ``features`` (e.g. feedback_submitted). The strength is
# the *item-level* claim strength; color and category claims are weaker by
# design (see the module docstring).
_BEHAVIOR_RULES: dict[str, dict[str, Any]] = {
    "item_replaced": {"polarity": "negative", "strength": 0.25},
    "item_rejected": {"polarity": "negative", "strength": 0.2},
    "outfit_rejected": {"polarity": "negative", "strength": 0.1},
    "outfit_selected": {"polarity": "positive", "strength": 0.5},
    "wardrobe_adopted": {"polarity": "positive", "strength": 0.5},
    "wardrobe_removed": {"polarity": "negative", "strength": 0.15},
    "feedback_submitted": {"polarity": None, "strength": 0.7},
}

# Category induction: a category-level claim appears only after this many
# *distinct* items of the same category show the same polarity.
CATEGORY_INDUCTION_THRESHOLD = 3
CATEGORY_INDUCTION_STRENGTH = 0.15
COLOR_POSITIVE_STRENGTH = 0.2
COLOR_NEGATIVE_STRENGTH = 0.1

_OCCASION_TERMS = (
    "通勤", "上班", "面试", "约会", "聚会", "婚礼", "晚宴", "旅游", "旅行",
    "运动", "健身", "逛街", "度假", "海边", "音乐会", "演唱会", "出差",
)
_FORMALITY_TERMS = (
    ("正式", "formal"),
    ("休闲", "casual"),
    ("简约", "minimal"),
    ("商务", "business"),
)

# Only behaviors that express an actual accept/reject judgement may generalize
# to a category. A plain replacement often means "this piece does not fit this
# outfit", not "I dislike every item of this category".
_CATEGORY_INDUCTION_SOURCES = {
    "item_rejected",
    "outfit_rejected",
    "outfit_selected",
    "wardrobe_adopted",
    "wardrobe_removed",
    "feedback_submitted",
}


def _occasions_from_request(request: str) -> list[str]:
    """Occasion words found in a request, used to scope weak evidence."""
    text = request or ""
    return [word for word in _OCCASION_TERMS if word in text]


def _formality_from_request(request: str) -> str:
    text = request or ""
    for word, tag in _FORMALITY_TERMS:
        if word in text:
            return tag
    return ""


def _scope_from_context(context: dict[str, Any] | None) -> dict[str, Any]:
    """A contextual scope carrying the scene words from the request.

    With no scene words the scope stays ``{"type": "contextual"}`` (ungated);
    the resolver then applies it unless a signature targets a different scene.
    """
    request = str((context or {}).get("request") or "")
    scope: dict[str, Any] = {"type": "contextual"}
    occasions = _occasions_from_request(request)
    if occasions:
        scope["occasions"] = occasions[:6]
    formality = _formality_from_request(request)
    if formality:
        scope["formality"] = formality
    return scope


def _scope_signature(scope: dict[str, Any] | None) -> tuple[str, tuple[str, ...], str]:
    """Canonical scene key used to keep weak behavior signals isolated."""
    value = scope or {}
    return (
        str(value.get("type") or "contextual"),
        tuple(sorted(str(item) for item in value.get("occasions") or [])),
        str(value.get("formality") or ""),
    )


def _target_item_ids(event: dict[str, Any]) -> list[str]:
    """Item ids the event is *about* (the piece being replaced/rejected/...)."""
    event_type = event["event_type"]
    features = event.get("features") or {}
    context = event.get("context") or {}
    if event_type == "item_replaced":
        return list(features.get("replaced_item_ids") or [])
    if event_type in ("outfit_rejected", "outfit_selected", "feedback_submitted"):
        return list(context.get("item_ids") or [])
    item_id = str(event.get("item_id") or "")
    return [item_id] if item_id else []


def _lookup_item_attrs(
    connection: Any, item_ids: list[str]
) -> dict[str, dict[str, str]]:
    """Map item_id -> {category, color} using the catalog (best effort)."""
    attrs: dict[str, dict[str, str]] = {}
    for row in catalog_repository.fetch_items_by_ids(connection, item_ids):
        category = str(row["main_category"] or "").strip() or str(row["item_type"] or "")
        attrs[row["item_id"]] = {
            "category": category,
            "color": str(row["color"] or "").strip(),
        }
    return attrs


def _category_induction_evidence(
    connection: Any,
    event: dict[str, Any],
    polarity: str,
    category: str,
    scope: dict[str, Any],
) -> dict[str, Any] | None:
    """A category-level claim once enough distinct items of the same category
    show the same polarity.

    Distinct item ids come from judgement events in the same scene. At least
    three different events are also required, so rejecting one whole outfit
    cannot immediately become a category preference. Existing induction rows
    define emitted milestones, making threshold crossings idempotent even when
    one event adds more than one new item.
    """
    if event["event_type"] not in _CATEGORY_INDUCTION_SOURCES:
        return None
    scope_key = _scope_signature(scope)
    history = list_evidence(connection, event["user_id"], limit=5000)
    item_events: dict[str, int | str] = {
        item_id: event.get("event_id") or f"current:{item_id}"
        for item_id in _target_item_ids(event)
        if item_id
    }
    for row in history:
        if (
            row["attribute"] == "item"
            and row["polarity"] == polarity
            and row["source"] in _CATEGORY_INDUCTION_SOURCES
            and _scope_signature(row.get("scope")) == scope_key
        ):
            item_events[row["value"]] = row.get("event_id") or row["evidence_id"]
    distinct = set(item_events)
    if not distinct:
        return None
    attrs = _lookup_item_attrs(connection, list(distinct))
    items_in_category = {
        item_id
        for item_id, attrs_by_id in attrs.items()
        if attrs_by_id.get("category") == category
    }
    count_in_category = len(items_in_category)
    if count_in_category < CATEGORY_INDUCTION_THRESHOLD:
        return None
    event_count = len({item_events[item_id] for item_id in items_in_category})
    if event_count < CATEGORY_INDUCTION_THRESHOLD:
        return None
    earned_milestones = count_in_category // CATEGORY_INDUCTION_THRESHOLD
    emitted_milestones = sum(
        1
        for row in history
        if row["attribute"] == "category"
        and row["value"] == category
        and row["polarity"] == polarity
        and row["source"] == "category_induction"
        and _scope_signature(row.get("scope")) == scope_key
    )
    if emitted_milestones >= earned_milestones:
        return None
    return {
        "dimension": "garment",
        "attribute": "category",
        "value": category,
        "polarity": polarity,
        "strength": CATEGORY_INDUCTION_STRENGTH,
        "scope": scope,
        "source": "category_induction",
        "event_id": event.get("event_id"),
    }


def behavior_evidence_from_event(
    connection: Any,
    event: dict[str, Any],
) -> list[dict[str, Any]]:
    """Convert one behavior event into standardized evidence dicts.

    ``event`` is the dict returned by ``interaction_event_repository``. The
    returned claims are weak by design and may be empty when the event carries
    no usable item signal (e.g. ``style_requested``).
    """
    event_type = event["event_type"]
    if event_type == "explicit_preference":
        return _explicit_evidence(event)
    rule = _BEHAVIOR_RULES.get(event_type)
    if rule is None:
        return []
    polarity = rule["polarity"]
    if polarity is None:
        sign = str((event.get("features") or {}).get("feedback") or "").strip().lower()
        if sign in ("positive", "good", "yes", "up"):
            polarity = "positive"
        elif sign in ("negative", "bad", "no", "down"):
            polarity = "negative"
        else:
            return []

    item_ids = _target_item_ids(event)
    if not item_ids:
        return []
    attrs = _lookup_item_attrs(connection, item_ids)
    scope = _scope_from_context(event.get("context"))
    strength = float(rule["strength"])
    color_strength = (
        COLOR_POSITIVE_STRENGTH if polarity == "positive" else COLOR_NEGATIVE_STRENGTH
    )

    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    induced_categories: set[str] = set()
    for item_id in item_ids:
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        item_attrs = attrs.get(item_id, {})
        # 1) The exact piece the user acted on.
        evidence.append(
            {
                "dimension": "garment",
                "attribute": "item",
                "value": item_id,
                "polarity": polarity,
                "strength": strength,
                "scope": scope,
                "source": event_type,
                "event_id": event.get("event_id"),
            }
        )
        # 2) Weak color attribution for the same piece.
        color = item_attrs.get("color")
        if color:
            evidence.append(
                {
                    "dimension": "appearance",
                    "attribute": "color",
                    "value": color,
                    "polarity": polarity,
                    "strength": color_strength,
                    "scope": scope,
                    "source": event_type,
                    "event_id": event.get("event_id"),
                }
            )
        # 3) Category induction (only after enough distinct items).
        category = item_attrs.get("category")
        if category and category not in induced_categories:
            induced_categories.add(category)
            induction = _category_induction_evidence(
                connection, event, polarity, category, scope
            )
            if induction:
                evidence.append(induction)
    return evidence


def _explicit_evidence(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Explicit statements carry their own claim in ``features``."""
    features = event.get("features") or {}
    attribute = str(features.get("attribute") or "").strip().lower()
    value = str(features.get("value") or "").strip().lower()
    if not attribute or not value:
        return []
    polarity = str(features.get("polarity") or "positive")
    strength = float(features.get("strength") or 0.9)
    return [
        {
            "dimension": str(features.get("dimension") or "shopping").strip().lower(),
            "attribute": attribute,
            "value": value,
            "polarity": polarity if polarity in ("positive", "negative") else "positive",
            "strength": strength if 0.0 <= strength <= 1.0 else 0.9,
            "scope": {"type": "global"},
            "source": "explicit_statement",
            "event_id": event.get("event_id"),
        }
    ]


def record_and_fold(
    connection: Any,
    user_id: str,
    event_type: str,
    *,
    item_id: str = "",
    context: dict[str, Any] | None = None,
    features: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record one raw behavior event, then fold its evidence into the model."""
    event = record_event(
        connection,
        user_id,
        event_type,
        item_id=item_id,
        context=context,
        features=features,
    )
    evidence = behavior_evidence_from_event(connection, event)
    apply_evidence(connection, user_id, evidence)
    return event


__all__ = [
    "extract_language_evidence",
    "CATEGORY_INDUCTION_THRESHOLD",
    "CATEGORY_INDUCTION_STRENGTH",
    "COLOR_POSITIVE_STRENGTH",
    "COLOR_NEGATIVE_STRENGTH",
    "behavior_evidence_from_event",
    "record_and_fold",
]
