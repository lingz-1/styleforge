"""Optional Redis cache for session-level outfit context.

Follows the external-service pattern used across the codebase (enabled switch +
``None`` degradation + swallowed exceptions): a disabled or unreachable Redis
must never break a request. PostgreSQL remains the source of truth; Redis is a
read-through cache that short-circuits multi-turn follow-ups that would
otherwise rescan every chat message.
"""

from __future__ import annotations

import json
from typing import Any

from styleforge.core.config import Settings

try:  # pragma: no cover - optional dependency
    import redis as _redis_module
except ImportError:  # pragma: no cover
    _redis_module = None

CACHE_KEY_PREFIX = "styleforge:session-outfit:"


def redis_client_from_settings(settings: Settings) -> Any | None:
    """Return a Redis client when enabled, or ``None`` when disabled.

    Like the vision/LLM clients this only builds the client object; no command
    is sent here, so a stopped Redis server surfaces lazily and is swallowed
    by the cache helpers below.
    """
    if not settings.redis_enabled or _redis_module is None:
        return None
    try:
        return _redis_module.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
    except Exception:  # pragma: no cover - malformed URL
        return None


def _cache_key(session_id: str) -> str:
    return CACHE_KEY_PREFIX + session_id


def set_session_outfit_cache(
    client: Any | None,
    session_id: str,
    outfit_context: dict[str, Any],
    ttl: int,
) -> None:
    """Write the outfit context under ``session_id`` with a TTL (best effort)."""
    if client is None:
        return
    try:
        client.setex(
            _cache_key(session_id),
            ttl,
            json.dumps(outfit_context, ensure_ascii=False),
        )
    except Exception:
        pass


def get_session_outfit_cache(
    client: Any | None,
    session_id: str,
) -> dict[str, Any] | None:
    """Return the cached outfit context, or ``None`` on miss/error."""
    if client is None:
        return None
    try:
        raw = client.get(_cache_key(session_id))
        if raw is None:
            return None
        value = json.loads(raw)
        if isinstance(value, dict):
            return value
    except Exception:
        pass
    return None


def delete_session_outfit_cache(client: Any | None, session_id: str) -> None:
    """Evict the cached outfit context (best effort)."""
    if client is None:
        return
    try:
        client.delete(_cache_key(session_id))
    except Exception:
        pass
