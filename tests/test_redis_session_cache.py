"""Session-outfit Redis cache: read-through, write-back, eviction, degradation.

Injected fake Redis + fake DB connection so the tests need no running Redis or
PostgreSQL. The DB remains the source of truth; Redis short-circuits the chat
message scan on multi-turn follow-ups.
"""

from __future__ import annotations

import dataclasses
import json

from styleforge.core.config import Settings
from styleforge.core.redis import (
    delete_session_outfit_cache,
    get_session_outfit_cache,
    redis_client_from_settings,
    set_session_outfit_cache,
)
from styleforge.services.chat_service import (
    cache_session_outfit,
    get_session_outfit_context,
)

OUTFIT_CONTEXT = {
    "current_outfit_id": "o-abc",
    "current_item_ids": ["i1", "i2"],
    "current_candidates": [{"outfit_id": "o-abc", "item_ids": ["i1", "i2"]}],
}


class FakeRedis:
    """Minimal Redis stand-in: setex/get/delete over an in-memory dict."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def setex(self, key: str, ttl: int, value: str) -> None:
        self._store[key] = value

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def __contains__(self, key: str) -> bool:
        return key in self._store


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class FakeConnection:
    """DB stand-in for ``active_outfit_messages``; records whether SQL ran."""

    def __init__(self, rows) -> None:
        self._rows = rows
        self.executed = False

    def execute(self, sql, params=None):
        self.executed = True
        return _FakeCursor(self._rows)


def _message_row(result: dict) -> dict:
    return {
        "message_id": "m1",
        "session_id": "sess1",
        "user_id": "u1",
        "role": "assistant",
        "content": "summary",
        "task_type": "outfit_recommend",
        "run_id": "r1",
        "result_json": json.dumps(result),
        "created_at": "2026-08-12T00:00:00+00:00",
    }


def _outfit_payload() -> dict:
    return {
        "task_type": "outfit_recommend",
        "result": {
            "structured_result": {
                "recommendations": [{"outfit_id": "o-abc", "item_ids": ["i1", "i2"]}]
            }
        },
    }


def _settings_with_redis() -> Settings:
    return dataclasses.replace(
        Settings.from_env(),
        redis_enabled=True,
        redis_url="redis://127.0.0.1:6379/0",
        redis_ttl=3600,
    )


def test_redis_client_from_settings_disabled_returns_none() -> None:
    settings = dataclasses.replace(Settings.from_env(), redis_enabled=False)
    assert redis_client_from_settings(settings) is None


def test_redis_client_from_settings_enabled_returns_client() -> None:
    settings = _settings_with_redis()
    assert redis_client_from_settings(settings) is not None


def test_set_get_delete_round_trip() -> None:
    fake = FakeRedis()
    set_session_outfit_cache(fake, "sess1", OUTFIT_CONTEXT, ttl=60)
    assert get_session_outfit_cache(fake, "sess1") == OUTFIT_CONTEXT
    delete_session_outfit_cache(fake, "sess1")
    assert get_session_outfit_cache(fake, "sess1") is None


def test_read_through_miss_scans_db_and_writes_back() -> None:
    fake = FakeRedis()
    connection = FakeConnection([_message_row(_outfit_payload())])
    context = get_session_outfit_context(
        connection, "u1", "sess1", redis=fake, redis_ttl=3600
    )
    assert context == OUTFIT_CONTEXT
    assert connection.executed is True
    assert get_session_outfit_cache(fake, "sess1") == OUTFIT_CONTEXT


def test_cache_hit_skips_db_scan() -> None:
    fake = FakeRedis()
    set_session_outfit_cache(fake, "sess1", OUTFIT_CONTEXT, ttl=3600)
    connection = FakeConnection([])  # empty DB would resolve to no outfit
    context = get_session_outfit_context(
        connection, "u1", "sess1", redis=fake, redis_ttl=3600
    )
    assert context == OUTFIT_CONTEXT
    assert connection.executed is False


def test_empty_context_is_not_cached() -> None:
    fake = FakeRedis()
    set_session_outfit_cache(fake, "sess1", OUTFIT_CONTEXT, ttl=3600)
    cache_session_outfit(
        fake, "sess1", {"current_outfit_id": "", "current_item_ids": []}, ttl=3600
    )
    # A follow-up that produced no outfit must not clobber the previous one.
    assert get_session_outfit_cache(fake, "sess1") == OUTFIT_CONTEXT


def test_db_scan_with_no_outfit_returns_empty_and_no_cache() -> None:
    fake = FakeRedis()
    connection = FakeConnection([_message_row({"task_type": "style_advice", "result": {}})])
    context = get_session_outfit_context(
        connection, "u1", "sess1", redis=fake, redis_ttl=3600
    )
    assert context == {"current_outfit_id": "", "current_item_ids": []}
    assert get_session_outfit_cache(fake, "sess1") is None


def test_redis_errors_are_swallowed_and_fall_back_to_db() -> None:
    class BrokenRedis(FakeRedis):
        def get(self, key: str) -> str | None:  # noqa: ARG002
            raise RuntimeError("redis down")

        def setex(self, key: str, ttl: int, value: str) -> None:  # noqa: ARG002
            raise RuntimeError("redis down")

    connection = FakeConnection([_message_row(_outfit_payload())])
    context = get_session_outfit_context(
        connection, "u1", "sess1", redis=BrokenRedis(), redis_ttl=3600
    )
    assert context == OUTFIT_CONTEXT
    assert connection.executed is True


def test_delete_evicts_cached_context() -> None:
    fake = FakeRedis()
    set_session_outfit_cache(fake, "sess1", OUTFIT_CONTEXT, ttl=3600)
    delete_session_outfit_cache(fake, "sess1")
    assert get_session_outfit_cache(fake, "sess1") is None


def test_none_redis_degrades_to_db_only() -> None:
    connection = FakeConnection([_message_row(_outfit_payload())])
    context = get_session_outfit_context(connection, "u1", "sess1", redis=None)
    assert context == OUTFIT_CONTEXT
    assert connection.executed is True
