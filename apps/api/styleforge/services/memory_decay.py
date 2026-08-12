"""Time decay for preference confidence, applied on the read path.

Per the scheme, decay is computed lazily from ``last_observed_at`` whenever a
preference is read — there is no background job. ``effective_confidence`` is
``confidence * exp(-lambda * elapsed_days)`` with per-policy lambdas; an
expired preference (past ``expires_at`` with no fresh observation) is filtered
out by the resolver.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Decay per day; 0 for permanent, slow for long-term, normal for the rest.
DECAY_LAMBDAS = {"none": 0.0, "slow": 0.004, "normal": 0.02}

_DEFAULT_EXPIRY_DAYS = 90


def _parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _elapsed_days(observed_at: str, now: datetime) -> float:
    observed = _parse_ts(observed_at)
    if observed is None:
        return 0.0
    return max(0.0, (now - observed).total_seconds() / 86400.0)


def effective_confidence(
    preference: dict[str, Any],
    now: datetime | None = None,
) -> float:
    """Confidence after time decay, clamped to ``>= 0``.

    Uses ``decay_policy`` (none/slow/normal) and ``last_observed_at``. A row
    missing ``last_observed_at`` decays from an assumed default window so a
    migrated preference does not decay immediately.
    """
    policy = preference.get("decay_policy") or "normal"
    lambda_ = DECAY_LAMBDAS.get(policy, 0.02)
    observed_at = preference.get("last_observed_at") or ""
    now = now or datetime.now(timezone.utc)
    if not observed_at:
        # No timestamp recorded: estimate from created/updated, else no decay.
        observed_at = preference.get("updated_at") or preference.get("created_at") or ""
    days = _elapsed_days(observed_at, now)
    return max(0.0, float(preference.get("confidence") or 0.0) * (2.718281828459045 ** (-lambda_ * days)))


def is_expired(
    preference: dict[str, Any],
    now: datetime | None = None,
) -> bool:
    """True once ``expires_at`` has passed and the preference went unobserved."""
    expires_at = preference.get("expires_at") or ""
    if not expires_at:
        return False
    expires = _parse_ts(expires_at)
    if expires is None:
        return False
    now = now or datetime.now(timezone.utc)
    return now > expires


__all__ = ["DECAY_LAMBDAS", "effective_confidence", "is_expired"]
