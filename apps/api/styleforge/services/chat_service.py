"""Helpers shared by session persistence and multi-turn context resolution."""

from __future__ import annotations

from typing import Any

from styleforge.core.redis import (
    get_session_outfit_cache,
    set_session_outfit_cache,
)
from styleforge.orchestration.task_router import TaskType
from styleforge.repositories.chat_repository import active_outfit_messages
from styleforge.repositories.database import Connection


def session_signals_from_request(request: str) -> dict[str, Any]:
    """Extract lightweight adjustment signals from a follow-up request.

    These feed the resolver's ``session_signals`` bucket so the next turn can
    honor the direction of the last modification (e.g. ``更休闲`` ->
    ``formality: decrease``) without re-deriving it.
    """
    text = request.lower()
    signals: dict[str, Any] = {}
    for slot_word in ("外套", "裤子", "裙子", "鞋", "包", "衬衫", "上衣", "连衣裙"):
        if slot_word in text:
            signals["target_slot"] = slot_word
            break
    if "正式" in text and ("更" in text or "再" in text or "一点" in text):
        signals["formality"] = "increase"
    elif "休闲" in text and ("更" in text or "再" in text or "一点" in text):
        signals["formality"] = "decrease"
    if "简约" in text and ("更" in text or "再" in text or "一点" in text):
        signals["style"] = "minimal"
    return signals


def outfit_context_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract the produced outfit from a task payload, or an empty context.

    The ``result`` may come from the semantic recommend path
    (``structured_result.recommendations``) or the deterministic fallback
    (``recommendations``), and from ``OutfitModifyResult.alternatives`` for
    modifications.  Any other task type contributes no outfit context.
    """
    task_type = str(payload.get("task_type", ""))
    result = payload.get("result")
    if not isinstance(result, dict):
        return {"current_outfit_id": "", "current_item_ids": []}
    if task_type == TaskType.OUTFIT_RECOMMEND.value:
        structured = result.get("structured_result")
        recommendations = None
        if isinstance(structured, dict):
            recommendations = structured.get("recommendations")
        if not recommendations:
            recommendations = result.get("recommendations")
        if isinstance(recommendations, list) and recommendations:
            first = recommendations[0]
            if isinstance(first, dict):
                return {
                    "current_outfit_id": str(first.get("outfit_id", "")),
                    "current_item_ids": list(first.get("item_ids", [])),
                }
        return {"current_outfit_id": "", "current_item_ids": []}
    if task_type == TaskType.OUTFIT_MODIFY.value:
        alternatives = result.get("alternatives")
        if isinstance(alternatives, list) and alternatives:
            first = alternatives[0]
            if isinstance(first, dict):
                return {
                    "current_outfit_id": str(result.get("current_outfit_id", "")),
                    "current_item_ids": list(first.get("item_ids", [])),
                    "session_signals": session_signals_from_request(
                        str(payload.get("request", ""))
                    ),
                }
        return {
            "current_outfit_id": "",
            "current_item_ids": [],
            "session_signals": {},
        }
    return {"current_outfit_id": "", "current_item_ids": []}


def assistant_summary(payload: dict[str, Any]) -> str:
    """Short bubble text for an assistant message."""
    result = payload.get("result")
    if not isinstance(result, dict):
        return str(payload.get("request", ""))
    task_type = str(payload.get("task_type", ""))
    if task_type == TaskType.OUTFIT_MODIFY.value:
        message = result.get("message")
        if message:
            return str(message)
    if task_type == TaskType.OUTFIT_RECOMMEND.value:
        structured = result.get("structured_result")
        if isinstance(structured, dict):
            advice = structured.get("advice")
            if isinstance(advice, list) and advice:
                return str(advice[0])
        recommendations = result.get("recommendations")
        if isinstance(recommendations, list) and recommendations:
            return f"为你推荐了 {len(recommendations)} 套搭配"
    summary = result.get("summary")
    if summary:
        return str(summary)
    return str(payload.get("request", ""))


def trim_message_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep only the fields a client needs to re-render a turn."""
    return {
        "run_id": payload.get("run_id", ""),
        "user_id": payload.get("user_id", ""),
        "request": payload.get("request", ""),
        "task_type": payload.get("task_type", ""),
        "status": payload.get("status", ""),
        "result": payload.get("result"),
    }


def get_session_outfit_context(
    connection: Connection,
    user_id: str,
    session_id: str,
    redis: Any | None = None,
    redis_ttl: int = 0,
) -> dict[str, Any]:
    """Resolve the current outfit from the session's most recent outfit turn.

    Read-through cache: a Redis hit returns immediately; a miss scans the chat
    messages and writes the result back so the next follow-up skips the scan.
    Any Redis error degrades to the database path.
    """
    if redis is not None:
        cached = get_session_outfit_cache(redis, session_id)
        if cached is not None:
            return cached
    for message in active_outfit_messages(connection, user_id, session_id):
        result = message.get("result")
        if not isinstance(result, dict):
            continue
        context = outfit_context_from_payload(result)
        if context["current_item_ids"]:
            if redis is not None and redis_ttl > 0:
                set_session_outfit_cache(redis, session_id, context, redis_ttl)
            return context
    # No outfit context: omit ``session_signals`` unless a turn carried some,
    # keeping the empty shape stable for callers that test it.
    return {"current_outfit_id": "", "current_item_ids": []}


def cache_session_outfit(
    redis: Any | None,
    session_id: str,
    outfit_context: dict[str, Any],
    ttl: int,
) -> None:
    """Write a just-produced outfit context into the Redis cache (best effort).

    Called right after the assistant message is persisted, so the next request
    in the same session hits Redis instead of rescanning the chat history.
    """
    if redis is None or ttl <= 0 or not outfit_context.get("current_item_ids"):
        return
    set_session_outfit_cache(redis, session_id, outfit_context, ttl)
