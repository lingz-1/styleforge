"""Memory Resolver: turn the raw preference model into per-agent Memory Packs.

The raw ``preference_model`` rows are a flat list; each agent needs a different
slice of them. ``resolve`` applies decay + expiry filtering, splits the rows
into the scheme's five buckets, matches contextual preferences against the
current request signature, then keeps only the buckets each agent role is
allowed to see. It is a pure function over in-memory preference dicts, so it is
easy to unit-test without a database.

Buckets (scheme section 8):
- ``session_signals``        real-time state from the current session (Redis)
- ``short_term_preferences`` short-lived preferences
- ``stable_preferences``     long-term / candidate preferences with high decayed confidence
- ``contextual_preferences`` preferences scoped to the current occasion
- ``avoidances``             what the user wants to avoid (negative polarity)
"""

from __future__ import annotations

from typing import Any

from styleforge.services.memory_decay import effective_confidence, is_expired

MEMORY_PACK_KEYS = (
    "session_signals",
    "short_term_preferences",
    "stable_preferences",
    "contextual_preferences",
    "avoidances",
)

AGENT_RETRIEVER = "retriever"
AGENT_COMPOSER = "composer"
AGENT_CRITIC = "critic"

# Which buckets each agent role is allowed to see.
AGENT_BUCKETS: dict[str, tuple[str, ...]] = {
    AGENT_RETRIEVER: MEMORY_PACK_KEYS,
    AGENT_COMPOSER: ("session_signals", "short_term_preferences", "stable_preferences"),
    AGENT_CRITIC: ("stable_preferences", "avoidances"),
    # H3a-4 PreferenceRetriever increments (legacy three keys untouched — the
    # PreferenceRetriever reuses this same resolver as its gate). "research" sees
    # no avoidances (it plans outfit context, not exclusions); the synthesizer
    # sees none at all (it only reorganizes existing evidence).
    "coordinator": ("session_signals", "stable_preferences"),
    "research": ("stable_preferences", "contextual_preferences"),
    "stylist": MEMORY_PACK_KEYS,
    "research_synthesizer": (),
}

# Confidence bars: effective confidence must clear these to be injected.
_SHORT_TERM_BAR = 0.25
_STABLE_BAR = 0.6
_AVOIDANCE_BARS = {AGENT_RETRIEVER: 0.3, AGENT_CRITIC: 0.5}
_BUCKET_LIMIT = 8


def _signature_text(request_signature: dict[str, Any] | None) -> str:
    """Flatten a request signature into one lowercase search string."""
    if not request_signature:
        return ""
    parts: list[str] = []
    for value in request_signature.values():
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(str(item) for item in value if item)
        elif isinstance(value, dict):
            parts.extend(str(item) for item in value.values() if item)
    return " ".join(parts).lower()


def _matches_contextual(
    scope: dict[str, Any],
    signature_text: str,
) -> bool:
    """A contextual preference applies when its tags show up in the request."""
    occasions = [str(occ).lower() for occ in (scope.get("occasions") or [])]
    if occasions:
        return any(occ in signature_text for occ in occasions)
    formality = str(scope.get("formality") or "").lower()
    if formality:
        return formality in signature_text
    # Ungated contextual preference: apply unless a signature exists that
    # clearly targets a different context. Keep it simple — include it.
    return True


def _bucket_rows(rows: list[dict[str, Any]], limit: int = _BUCKET_LIMIT) -> list[dict[str, Any]]:
    return rows[:limit]


def resolve(
    preferences: list[dict[str, Any]],
    request_signature: dict[str, Any] | None = None,
    *,
    agent_role: str = AGENT_RETRIEVER,
    session_signals: dict[str, Any] | None = None,
    now: Any = None,
) -> dict[str, Any]:
    """Split preferences into a per-agent Memory Pack.

    ``preferences`` is the flat, *raw* list from ``preference_model`` (the rows
    are decayed here, so the same list can feed multiple agents). Returns a dict
    with the five buckets; buckets the agent is not allowed to see are empty.
    """
    if agent_role not in AGENT_BUCKETS:
        raise ValueError(f"unknown agent role: {agent_role}")

    signature_text = _signature_text(request_signature)
    scored: list[tuple[float, dict[str, Any]]] = []
    for pref in preferences:
        if not pref.get("active", True):
            continue
        if is_expired(pref, now):
            continue
        effective = effective_confidence(pref, now)
        # Present the decayed confidence to the consumers.
        item = {**pref, "confidence": round(effective, 4)}
        scored.append((effective, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)

    avoidances_bar = _AVOIDANCE_BARS.get(agent_role, 0.3)
    buckets: dict[str, list[Any]] = {key: [] for key in MEMORY_PACK_KEYS}
    buckets["session_signals"] = dict(session_signals or {})
    for effective, item in scored:
        scope = item.get("scope") or {}
        lifecycle = item.get("lifecycle") or "short_term"
        polarity = item.get("polarity") or "positive"
        if polarity == "negative":
            if effective >= avoidances_bar:
                buckets["avoidances"].append(item)
            continue
        if scope.get("type") == "contextual":
            # Context-gate first: a scene preference must never leak into a
            # different scene's pack, whatever its lifecycle. A long-term scene
            # preference that matches here is also stable, so it additionally
            # lands in stable_preferences for the stable-only agents.
            if not _matches_contextual(scope, signature_text):
                continue
            buckets["contextual_preferences"].append(item)
            if lifecycle in ("long_term_candidate", "long_term") and effective >= _STABLE_BAR:
                buckets["stable_preferences"].append(item)
            continue
        if lifecycle in ("long_term_candidate", "long_term"):
            if effective >= _STABLE_BAR:
                buckets["stable_preferences"].append(item)
            continue
        # short_term
        if effective >= _SHORT_TERM_BAR:
            buckets["short_term_preferences"].append(item)

    allowed = AGENT_BUCKETS[agent_role]
    pack: dict[str, Any] = {}
    for key in MEMORY_PACK_KEYS:
        if key not in allowed:
            pack[key] = {}
        elif key == "session_signals":
            # A dict, not a list: pass through as-is (already capped by design).
            pack[key] = buckets[key]
        else:
            pack[key] = _bucket_rows(buckets[key])
    return pack


__all__ = [
    "MEMORY_PACK_KEYS",
    "AGENT_RETRIEVER",
    "AGENT_COMPOSER",
    "AGENT_CRITIC",
    "AGENT_BUCKETS",
    "resolve",
]
