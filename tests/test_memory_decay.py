"""Tests for read-path confidence decay (pure functions, no DB)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from styleforge.services.memory_decay import DECAY_LAMBDAS, effective_confidence, is_expired


def _pref(**overrides) -> dict:
    base = {
        "confidence": 0.8,
        "decay_policy": "normal",
        "last_observed_at": "",
        "updated_at": "",
        "expires_at": "",
    }
    base.update(overrides)
    return base


def test_none_policy_never_decays() -> None:
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=200)).isoformat()
    assert effective_confidence(_pref(confidence=0.9, decay_policy="none", last_observed_at=old), now) == pytest.approx(0.9)


def test_slow_and_normal_follow_exponential_curve() -> None:
    now = datetime.now(timezone.utc)
    days = 30
    observed = (now - timedelta(days=days)).isoformat()
    e = 2.718281828459045
    slow = effective_confidence(
        _pref(confidence=1.0, decay_policy="slow", last_observed_at=observed), now
    )
    normal = effective_confidence(
        _pref(confidence=1.0, decay_policy="normal", last_observed_at=observed), now
    )
    assert slow == pytest.approx(e ** (-DECAY_LAMBDAS["slow"] * days))
    assert normal == pytest.approx(e ** (-DECAY_LAMBDAS["normal"] * days))
    assert slow > normal  # long-term decays more gently


def test_missing_timestamp_does_not_decay_immediately() -> None:
    now = datetime.now(timezone.utc)
    pref = _pref(confidence=0.7, decay_policy="normal", last_observed_at="")
    assert effective_confidence(pref, now) == pytest.approx(0.7)


def test_is_expired_after_expires_at() -> None:
    now = datetime.now(timezone.utc)
    past = (now - timedelta(days=1)).isoformat()
    future = (now + timedelta(days=1)).isoformat()
    assert is_expired(_pref(expires_at=past), now)
    assert not is_expired(_pref(expires_at=future), now)
    assert not is_expired(_pref(expires_at=""), now)
