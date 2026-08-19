"""PreferenceRetriever (H3a-4): layered Top-K recall of long-term preferences.

Read-chain priority (frozen 原则 b): Current Turn (D layer) → Thread
(``thread_context``) → Short-term → Contextual → Stable → Avoidances. This
retriever serves ONLY the long-term tail; the Thread layer lives in
``thread_context`` and the two chains NEVER promote into each other.

It reuses the legacy ``memory_resolver.resolve`` as its gate (decay / expiry /
contextual matching / confidence bars / per-agent bucket mask), then re-ranks
each returned pack into a flat, layer-labelled list capped per layer and
rendered in read-chain order (total ``top_k`` = 8).

Iron rule (frozen): this module is read-chain only — it imports no
``apply_evidence`` / ``preference_model_repository`` symbols and never writes to
the user profile. A turn-scoped preference ("这次想穿黑一点") lives in
ThreadPreferenceView and never reaches ``raw_preferences``.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from styleforge.services.memory_resolver import AGENT_BUCKETS, resolve as resolve_pack

# Layer render order = read-chain priority; per-layer caps (total top_k = 8).
_LAYER_ORDER = (
    "short_term_preferences",
    "contextual_preferences",
    "stable_preferences",
    "avoidances",
)
_LAYER_CAPS = {
    "short_term_preferences": 3,
    "contextual_preferences": 3,
    "stable_preferences": 4,
    "avoidances": 3,
}
_LAYER_LABELS = {
    "short_term_preferences": "短期偏好",
    "contextual_preferences": "场景偏好",
    "stable_preferences": "长期偏好",
    "avoidances": "避免",
}
_TOP_K = 8
_SCOPE_WEIGHT = 1.1  # a contextual preference is current-scene relevant
_RECENCY_DECAY = 0.01  # exp(-0.01 * days)


def _parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _entry_terms(entry: dict[str, Any]) -> list[str]:
    """Semantic terms used for relevance: dimension/value + scope tags."""
    terms: list[str] = []
    for key in ("dimension", "category", "attribute", "value", "content"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            terms.append(value.strip().lower())
    scope = entry.get("scope") or {}
    for key in ("occasions", "formality", "style", "category"):
        value = scope.get(key)
        if isinstance(value, str) and value.strip():
            terms.append(value.strip().lower())
        elif isinstance(value, list):
            terms.extend(str(item).lower() for item in value if item)
    return terms


class PreferenceRetriever:
    """No-DB layered recall over the raw preference list (read-chain tail)."""

    def __init__(self, *, top_k: int = _TOP_K, now: Any = None) -> None:
        self.top_k = top_k
        self._now = now

    def retrieve(self, agent: str, state: dict[str, Any]) -> list[dict[str, Any]]:
        """Rank + flatten ``state["raw_preferences"]`` by read-chain layer."""
        if agent not in AGENT_BUCKETS:
            return []
        raw = list(state.get("raw_preferences") or [])
        if not raw:
            return []
        request_text = str(state.get("request") or "")
        signature = {"practical_context": request_text} if request_text.strip() else None
        pack = resolve_pack(
            raw,
            request_signature=signature,
            agent_role=agent,
            session_signals=(state.get("thread_context") or {}).get("session_signals"),
            now=self._now,
        )
        flat: list[dict[str, Any]] = []
        for layer in _LAYER_ORDER:
            entries = list(pack.get(layer) or [])
            ranked = sorted(
                entries,
                key=lambda item: self._score(item, request_text),
                reverse=True,
            )
            for entry in ranked[:_LAYER_CAPS[layer]]:
                flat.append({**entry, "layer": layer, "layer_label": _LAYER_LABELS[layer]})
                if len(flat) >= self.top_k:
                    return flat
        return flat

    # -- scoring ------------------------------------------------------------

    def _score(self, entry: dict[str, Any], request_text: str) -> float:
        confidence = float(entry.get("confidence") or 0.0)
        relevance = self._relevance(entry, request_text)
        scope = (entry.get("scope") or {}).get("type")
        scope_weight = _SCOPE_WEIGHT if scope == "contextual" else 1.0
        return confidence * relevance * scope_weight * self._recency(entry)

    def _relevance(self, entry: dict[str, Any], request_text: str) -> float:
        """1.0 when the entry's terms appear in the request, else 0.75."""
        terms = _entry_terms(entry)
        if not terms:
            return 0.8
        if any(term and term in request_text for term in terms):
            return 1.0
        return 0.75

    def _recency(self, entry: dict[str, Any]) -> float:
        observed = (
            entry.get("last_observed_at")
            or entry.get("updated_at")
            or entry.get("created_at")
            or ""
        )
        parsed = _parse_ts(observed)
        if parsed is None:
            return 1.0
        now = self._now or datetime.now(timezone.utc)
        try:
            days = max(0.0, (now - parsed).total_seconds() / 86400.0)
        except TypeError:
            return 1.0
        return math.exp(-_RECENCY_DECAY * days)


__all__ = ["PreferenceRetriever"]
