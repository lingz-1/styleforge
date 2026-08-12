"""Tests for the semantic-generalization boundary of the memory system.

Covers the scheme's layering (``不喜欢当前这件`` != ``不喜欢这一品类`` !=
``不喜欢这个属性`` != ``长期不喜欢``): item-level claims, category induction,
color attribution, contextual activation, lifecycle/confidence decoupling,
session signals, and conflict re-interpretation.
"""

from __future__ import annotations

import pytest

from styleforge.repositories import preference_evidence_repository
from styleforge.repositories.catalog_repository import upsert_items
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.interaction_event_repository import record_event
from styleforge.repositories.preference_model_repository import (
    get_preference_by_key,
    list_preferences,
    upsert_preference,
)
from styleforge.services.memory_aggregator import apply_evidence
from styleforge.services.memory_evidence import behavior_evidence_from_event
from styleforge.services.memory_resolver import AGENT_RETRIEVER, resolve

from tests.helpers import make_item


def _fold(connection, user_id: str, event_type: str, **kwargs) -> None:
    event = record_event(connection, user_id, event_type, **kwargs)
    apply_evidence(
        connection, user_id, behavior_evidence_from_event(connection, event)
    )


def test_single_rejection_never_forms_category_avoidance(db_dsn) -> None:
    """One rejected piece folds to the item level, scoped contextually."""
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        upsert_items(connection, [make_item("dress-1", "dress", "连衣裙一", "red")], "test")
        _fold(
            connection, "u", "outfit_rejected",
            context={"request": "约会", "item_ids": ["dress-1"]},
        )
        prefs = list_preferences(connection, "u")
        assert any(
            p["attribute"] == "item"
            and p["value"] == "dress-1"
            and p["polarity"] == "negative"
            for p in prefs
        )
        # No category-level claim for a single piece, and no global scope leaks.
        assert not any(p["attribute"] == "category" for p in prefs)
        assert all((p["scope"] or {}).get("type") == "contextual" for p in prefs)


def test_category_induction_after_three_distinct_items(db_dsn) -> None:
    """Three distinct dresses rejected -> a category-level negative hypothesis."""
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        upsert_items(
            connection,
            [make_item(f"dress-{i}", "dress", f"连衣裙{i}", "red") for i in (1, 2, 3)],
            "test",
        )
        for item_id in ("dress-1", "dress-2", "dress-3"):
            _fold(
                connection, "u", "outfit_rejected",
                context={"request": "约会", "item_ids": [item_id]},
            )
        prefs = list_preferences(connection, "u")
        category = next((p for p in prefs if p["attribute"] == "category"), None)
        assert category is not None
        assert category["value"] == "dress"
        assert category["polarity"] == "negative"
        # The item-level rows are still tracked individually.
        assert sum(1 for p in prefs if p["attribute"] == "item") == 3


def test_color_attribution_on_selection(db_dsn) -> None:
    """Selecting a grey coat also teaches a weak color-family preference."""
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        upsert_items(connection, [make_item("coat-1", "coat", "灰色大衣", "gray")], "test")
        _fold(
            connection, "u", "outfit_selected",
            context={"request": "通勤面试", "item_ids": ["coat-1"]},
        )
        prefs = list_preferences(connection, "u")
        assert any(
            p["attribute"] == "color"
            and p["value"] == "gray"
            and p["polarity"] == "positive"
            for p in prefs
        )


def test_resolver_activates_different_occasion_preferences(db_dsn) -> None:
    """Work=极简 and weekend=复古 coexist: the resolver activates per scene."""
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        upsert_preference(
            connection, "u", dimension="style", attribute="style", value="极简",
            lifecycle="long_term", confidence=0.9,
            scope={"type": "contextual", "occasions": ["工作"]},
        )
        upsert_preference(
            connection, "u", dimension="style", attribute="style", value="复古",
            lifecycle="long_term", confidence=0.9,
            scope={"type": "contextual", "occasions": ["周末"]},
        )
        prefs = list_preferences(connection, "u")
        work = resolve(prefs, {"practical_context": "工作日上班"}, agent_role=AGENT_RETRIEVER)
        weekend = resolve(prefs, {"practical_context": "周末逛街"}, agent_role=AGENT_RETRIEVER)
        assert [p["value"] for p in work["contextual_preferences"]] == ["极简"]
        assert [p["value"] for p in weekend["contextual_preferences"]] == ["复古"]
        # The scene preference must not leak across contexts — 复古 stays out of
        # the work pack and 极简 stays out of the weekend pack, in every bucket.
        work_values = {
            p["value"]
            for bucket in ("stable_preferences", "short_term_preferences", "avoidances")
            for p in work[bucket]
            if isinstance(work[bucket], list)
        }
        weekend_values = {
            p["value"]
            for bucket in ("stable_preferences", "short_term_preferences", "avoidances")
            for p in weekend[bucket]
            if isinstance(weekend[bucket], list)
        }
        assert "复古" not in work_values
        assert "极简" not in weekend_values


def test_high_confidence_short_term_stays_short_term(db_dsn) -> None:
    """"最近想穿亮一点" is high-confidence but must not read as a stable rule."""
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        upsert_preference(
            connection, "u", dimension="style", attribute="style", value="亮色",
            lifecycle="short_term", confidence=0.95,
        )
        prefs = list_preferences(connection, "u")
        pack = resolve(prefs, agent_role=AGENT_RETRIEVER)
        assert any(p["value"] == "亮色" for p in pack["short_term_preferences"])
        assert not any(p["value"] == "亮色" for p in pack["stable_preferences"])


def test_modify_direction_only_enters_session_signals(db_dsn) -> None:
    """"这套更休闲一点" carries the direction, never a cross-session preference."""
    initialize_database(db_dsn)
    from styleforge.services.chat_service import session_signals_from_request

    signals = session_signals_from_request("更休闲一点")
    assert signals == {"formality": "decrease"}
    pack = resolve([], agent_role=AGENT_RETRIEVER, session_signals=signals)
    assert pack["session_signals"] == signals
    assert pack["short_term_preferences"] == []
    assert pack["stable_preferences"] == []


def test_later_explicit_liking_dress_overrides_rejection(db_dsn) -> None:
    """An explicit "I love dresses" later outweighs the weak induction negatives."""
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        upsert_items(
            connection,
            [make_item(f"dress-{i}", "dress", f"连衣裙{i}", "red") for i in (1, 2, 3)],
            "test",
        )
        for item_id in ("dress-1", "dress-2", "dress-3"):
            _fold(
                connection, "u", "outfit_rejected",
                context={"request": "约会", "item_ids": [item_id]},
            )
        _fold(
            connection, "u", "explicit_preference",
            features={"dimension": "garment", "attribute": "category",
                      "value": "dress", "polarity": "positive", "strength": 0.9},
        )
        pref = get_preference_by_key(connection, "u", "garment", "category", "dress")
        assert pref is not None
        # explicit 0.9 beats the 0.15 induction negatives on the same key.
        assert pref["polarity"] == "positive"
        assert pref["lifecycle"] == "long_term"


def test_cross_value_global_conflict_is_softened(db_dsn) -> None:
    """Opposite global claims on the same attribute soften and tag each other."""
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        _fold(
            connection, "u", "explicit_preference",
            features={"dimension": "style", "attribute": "style",
                      "value": "极简", "polarity": "positive", "strength": 0.9},
        )
        _fold(
            connection, "u", "explicit_preference",
            features={"dimension": "style", "attribute": "style",
                      "value": "复古", "polarity": "negative", "strength": 0.9},
        )
        prefs = {p["value"]: p for p in list_preferences(connection, "u")}
        assert prefs["极简"]["source_summary"].get("conflict_with") == ["复古"]
        assert prefs["复古"]["source_summary"].get("conflict_with") == ["极简"]
        # Both sides were softened below the un-conflicted explicit confidence.
        assert prefs["极简"]["confidence"] < 0.61
        assert prefs["复古"]["confidence"] < 0.61


def test_global_conflict_ignores_contextual_opposite(db_dsn) -> None:
    """A contextual opposite claim is isolated by its scene: it must neither tag
    nor soften a *global* preference of the same attribute (only same-context
    contradictions are real conflicts)."""
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        apply_evidence(connection, "u", [
            {
                "dimension": "style", "attribute": "style", "value": "极简",
                "polarity": "positive", "strength": 0.9, "scope": {"type": "global"},
                "source": "explicit_statement",
            },
            {
                "dimension": "style", "attribute": "style", "value": "复古",
                "polarity": "negative", "strength": 0.9,
                "scope": {"type": "contextual", "occasions": ["上班"]},
                "source": "llm_request",
            },
        ])
        prefs = {p["value"]: p for p in list_preferences(connection, "u")}
        # Global 极简 keeps its full explicit confidence and carries no marker.
        assert not prefs["极简"]["source_summary"].get("conflict_with")
        assert prefs["极简"]["confidence"] == pytest.approx(0.6036, abs=1e-3)
        # The contextual row is not penalized either.
        assert not prefs["复古"]["source_summary"].get("conflict_with")
        assert prefs["复古"]["confidence"] == pytest.approx(0.6036, abs=1e-3)


def test_preference_memory_is_isolated_between_users(db_dsn) -> None:
    """Events, evidence and preferences never leak across users; a contradiction
    written by one user must not mark another user's rows."""
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        apply_evidence(connection, "u1", [
            {
                "dimension": "style", "attribute": "style", "value": "极简",
                "polarity": "positive", "strength": 0.9, "scope": {"type": "global"},
                "source": "explicit_statement",
            }
        ])
        apply_evidence(connection, "u2", [
            {
                "dimension": "style", "attribute": "style", "value": "复古",
                "polarity": "positive", "strength": 0.9, "scope": {"type": "global"},
                "source": "explicit_statement",
            }
        ])
        assert [p["value"] for p in list_preferences(connection, "u1")] == ["极简"]
        assert [p["value"] for p in list_preferences(connection, "u2")] == ["复古"]
        assert len(preference_evidence_repository.list_evidence(connection, "u1")) == 1
        assert len(preference_evidence_repository.list_evidence(connection, "u2")) == 1

        # u2 contradicts u1's value: u1's row must stay untouched.
        apply_evidence(connection, "u2", [
            {
                "dimension": "style", "attribute": "style", "value": "极简",
                "polarity": "negative", "strength": 0.9, "scope": {"type": "global"},
                "source": "explicit_statement",
            }
        ])
        u1 = {p["value"]: p for p in list_preferences(connection, "u1")}
        assert not u1["极简"]["source_summary"].get("conflict_with")
        assert u1["极简"]["confidence"] == pytest.approx(0.6036, abs=1e-3)
