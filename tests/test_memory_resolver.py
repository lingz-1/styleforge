"""Tests for the Memory Resolver and per-agent bucket differentiation."""

from __future__ import annotations

import pytest

from styleforge.services.memory_resolver import (
    AGENT_COMPOSER,
    AGENT_CRITIC,
    AGENT_RETRIEVER,
    MEMORY_PACK_KEYS,
    resolve,
)


def _pref(**overrides) -> dict:
    base = {
        "dimension": "style",
        "attribute": "style",
        "value": "简约",
        "polarity": "positive",
        "lifecycle": "short_term",
        "scope": {"type": "global"},
        "confidence": 0.8,
        "decay_policy": "none",
        "last_observed_at": "2026-01-01T00:00:00+00:00",
        "expires_at": "",
        "active": True,
    }
    base.update(overrides)
    return base


def test_retriever_sees_all_buckets() -> None:
    prefs = [
        _pref(lifecycle="long_term", confidence=0.9),
        _pref(polarity="negative", value="皮革", confidence=0.7),
        _pref(scope={"type": "contextual", "occasions": ["通勤"]}, value="西装"),
    ]
    pack = resolve(
        prefs, request_signature={"practical_context": "通勤面试"}, agent_role=AGENT_RETRIEVER
    )
    assert pack["stable_preferences"]
    assert pack["avoidances"]
    assert pack["contextual_preferences"]
    assert pack["session_signals"] == {}


def test_composer_hides_avoidances_and_contextual() -> None:
    prefs = [
        _pref(lifecycle="long_term", confidence=0.9),
        _pref(polarity="negative", value="皮革", confidence=0.7),
        _pref(scope={"type": "contextual", "occasions": ["通勤"]}, value="西装"),
    ]
    pack = resolve(
        prefs, request_signature={"practical_context": "通勤面试"}, agent_role=AGENT_COMPOSER
    )
    assert pack["stable_preferences"]
    # Short-term is allowed for the composer; avoidance/contextual buckets are not.
    assert pack["short_term_preferences"] == []
    assert pack["avoidances"] == {}
    assert pack["contextual_preferences"] == {}


def test_critic_keeps_only_stable_high_confidence_and_avoidances() -> None:
    prefs = [
        _pref(lifecycle="long_term", confidence=0.9),
        _pref(lifecycle="long_term_candidate", confidence=0.55),  # below stable bar 0.6
        _pref(polarity="negative", value="皮革", confidence=0.6),
        _pref(polarity="negative", value="花哨", confidence=0.3),  # below critic bar 0.5
        _pref(lifecycle="short_term", confidence=0.8),
    ]
    pack = resolve(prefs, agent_role=AGENT_CRITIC)
    assert len(pack["stable_preferences"]) == 1
    assert pack["stable_preferences"][0]["value"] == "简约"
    assert [item["value"] for item in pack["avoidances"]] == ["皮革"]
    # Only stable + avoidances are visible to the critic; other buckets are withheld.
    assert pack["short_term_preferences"] == {}
    assert pack["contextual_preferences"] == {}
    assert pack["session_signals"] == {}


def test_session_signals_passthrough() -> None:
    prefs = [_pref(lifecycle="long_term", confidence=0.9)]
    pack = resolve(
        prefs,
        agent_role=AGENT_RETRIEVER,
        session_signals={"formality": "decrease", "target_slot": "外套"},
    )
    assert pack["session_signals"] == {"formality": "decrease", "target_slot": "外套"}


def test_unknown_role_raises() -> None:
    with pytest.raises(ValueError, match="unknown agent role"):
        resolve([], agent_role="nope")


def test_contextual_preference_matches_formality_scope() -> None:
    prefs = [_pref(scope={"type": "contextual", "formality": "正式"}, value="西装")]
    pack = resolve(
        prefs, request_signature={"practical_context": "正式晚宴"}, agent_role=AGENT_RETRIEVER
    )
    assert pack["contextual_preferences"]


def test_contextual_preference_ignored_when_no_match() -> None:
    prefs = [_pref(scope={"type": "contextual", "occasions": ["约会"]}, value="连衣裙")]
    pack = resolve(
        prefs, request_signature={"practical_context": "通勤"}, agent_role=AGENT_RETRIEVER
    )
    assert pack["contextual_preferences"] == []


def test_expired_preference_is_filtered_out() -> None:
    prefs = [
        _pref(lifecycle="long_term", confidence=0.9, expires_at="2000-01-01T00:00:00+00:00")
    ]
    pack = resolve(prefs, agent_role=AGENT_RETRIEVER)
    assert pack["stable_preferences"] == []
    assert pack["short_term_preferences"] == []


def test_pack_has_all_five_bucket_keys() -> None:
    pack = resolve([], agent_role=AGENT_RETRIEVER)
    assert list(pack.keys()) == list(MEMORY_PACK_KEYS)
