"""Helpers shared by session persistence and multi-turn context resolution."""

from __future__ import annotations

import sqlite3
from typing import Any

from styleforge.orchestration.task_router import TaskType
from styleforge.repositories.chat_repository import active_outfit_messages


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
                }
        return {"current_outfit_id": "", "current_item_ids": []}
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
    connection: sqlite3.Connection,
    user_id: str,
    session_id: str,
) -> dict[str, Any]:
    """Resolve the current outfit from the session's most recent outfit turn."""
    for message in active_outfit_messages(connection, user_id, session_id):
        result = message.get("result")
        if not isinstance(result, dict):
            continue
        context = outfit_context_from_payload(result)
        if context["current_item_ids"]:
            return context
    return {"current_outfit_id": "", "current_item_ids": []}
