"""User evaluation-profile persistence backed by the user_preferences table.

The v1.1 evaluation spec lets the user tune the importance of the five
existing evaluation dimensions. Weights live under ``evaluation_profile.weights``
inside ``preference_json`` and are normalized before storage.
"""

from __future__ import annotations

import json
from styleforge.repositories.database import Connection
from datetime import datetime, timezone
from typing import Any

from styleforge.core.rubric import DEFAULT_EVALUATION_WEIGHTS, normalize_weights


def get_evaluation_weights(
    connection: Connection,
    user_id: str,
) -> dict[str, float]:
    row = connection.execute(
        "SELECT preference_json FROM user_preferences WHERE user_id = %s",
        (user_id,),
    ).fetchone()
    if row is None:
        return dict(DEFAULT_EVALUATION_WEIGHTS)
    try:
        preferences = json.loads(row["preference_json"])
        weights = preferences.get("evaluation_profile", {}).get("weights", {})
    except (json.JSONDecodeError, AttributeError):
        return dict(DEFAULT_EVALUATION_WEIGHTS)
    return normalize_weights(weights)


def save_evaluation_weights(
    connection: Connection,
    user_id: str,
    weights: dict[str, Any],
) -> dict[str, float]:
    if not user_id.strip():
        raise ValueError("user_id cannot be empty")
    normalized = normalize_weights(weights)
    row = connection.execute(
        "SELECT preference_json FROM user_preferences WHERE user_id = %s",
        (user_id,),
    ).fetchone()
    preferences: dict[str, Any] = {}
    if row is not None:
        try:
            preferences = json.loads(row["preference_json"])
        except json.JSONDecodeError:
            preferences = {}
    preferences["evaluation_profile"] = {"weights": normalized}
    connection.execute(
        """
        INSERT INTO user_preferences(user_id, preference_json, updated_at)
        VALUES (%s, %s, %s)
        ON CONFLICT(user_id) DO UPDATE SET
            preference_json = excluded.preference_json,
            updated_at = excluded.updated_at
        """,
        (
            user_id,
            json.dumps(preferences, ensure_ascii=False, sort_keys=True),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    return normalized


def get_environment_profile(
    connection: Connection,
    user_id: str,
) -> dict[str, str]:
    """Read the user's default city/timezone for weather location resolution."""
    row = connection.execute(
        "SELECT preference_json FROM user_preferences WHERE user_id = %s",
        (user_id,),
    ).fetchone()
    if row is None:
        return {}
    try:
        preferences = json.loads(row["preference_json"])
        profile = preferences.get("environment_profile", {})
    except (json.JSONDecodeError, AttributeError):
        return {}
    default_city = str(profile.get("default_city", "")).strip()
    timezone = str(profile.get("timezone", "")).strip()
    return {
        "default_city": default_city,
        "timezone": timezone,
    }


def save_environment_profile(
    connection: Connection,
    user_id: str,
    *,
    default_city: str,
    timezone: str = "",
) -> dict[str, str]:
    if not user_id.strip():
        raise ValueError("user_id cannot be empty")
    default_city = default_city.strip()
    timezone = timezone.strip()
    row = connection.execute(
        "SELECT preference_json FROM user_preferences WHERE user_id = %s",
        (user_id,),
    ).fetchone()
    preferences: dict[str, Any] = {}
    if row is not None:
        try:
            preferences = json.loads(row["preference_json"])
        except json.JSONDecodeError:
            preferences = {}
    preferences["environment_profile"] = {
        "default_city": default_city,
        "timezone": timezone,
    }
    connection.execute(
        """
        INSERT INTO user_preferences(user_id, preference_json, updated_at)
        VALUES (%s, %s, %s)
        ON CONFLICT(user_id) DO UPDATE SET
            preference_json = excluded.preference_json,
            updated_at = excluded.updated_at
        """,
        (
            user_id,
            json.dumps(preferences, ensure_ascii=False, sort_keys=True),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    return {"default_city": default_city, "timezone": timezone}
