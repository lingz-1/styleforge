"""Persistent chat sessions and messages for multi-turn conversations.

A session groups an ordered exchange of ``chat_messages`` under one
``session_id``.  The assistant ``result_json`` stores a trimmed payload so a
client can re-render a previous turn (including the produced outfit context)
after a refresh or on another device.
"""

from __future__ import annotations

import json
from styleforge.repositories.database import Connection, Row
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from styleforge.orchestration.task_router import TaskType

OUTFIT_TASK_TYPES = (TaskType.OUTFIT_RECOMMEND.value, TaskType.OUTFIT_MODIFY.value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_chat_session(
    connection: Connection,
    *,
    user_id: str,
    title: str = "",
) -> dict[str, Any]:
    if not user_id.strip():
        raise ValueError("user_id cannot be empty")
    session_id = str(uuid4())
    timestamp = _now()
    connection.execute(
        """
        INSERT INTO chat_sessions(session_id, user_id, title, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (session_id, user_id, title.strip(), timestamp, timestamp),
    )
    return {
        "session_id": session_id,
        "user_id": user_id,
        "title": title.strip(),
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def _row_to_session(row: Row) -> dict[str, Any]:
    return {
        "session_id": row["session_id"],
        "user_id": row["user_id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_chat_sessions(
    connection: Connection,
    user_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT s.session_id, s.user_id, s.title, s.created_at, s.updated_at,
               m.content AS last_message,
               m.role AS last_role,
               m.created_at AS last_message_at
        FROM chat_sessions AS s
        LEFT JOIN chat_messages AS m ON m.message_id = (
            SELECT cm.message_id
            FROM chat_messages AS cm
            WHERE cm.session_id = s.session_id
            ORDER BY cm.created_at DESC, cm.message_seq DESC
            LIMIT 1
        )
        WHERE s.user_id = %s
        ORDER BY s.updated_at DESC
        LIMIT %s
        """,
        (user_id, limit),
    ).fetchall()
    sessions = []
    for row in rows:
        session = _row_to_session(row)
        if row["last_message"] is not None:
            session["last_message"] = {
                "content": row["last_message"],
                "role": row["last_role"],
                "created_at": row["last_message_at"],
            }
        sessions.append(session)
    return sessions


def get_chat_session(
    connection: Connection,
    user_id: str,
    session_id: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT session_id, user_id, title, created_at, updated_at
        FROM chat_sessions
        WHERE session_id = %s AND user_id = %s
        """,
        (session_id, user_id),
    ).fetchone()
    return _row_to_session(row) if row is not None else None


def rename_chat_session(
    connection: Connection,
    user_id: str,
    session_id: str,
    title: str,
) -> dict[str, Any] | None:
    title = title.strip()
    if not title:
        raise ValueError("title cannot be empty")
    row = connection.execute(
        """
        UPDATE chat_sessions
        SET title = %s, updated_at = %s
        WHERE session_id = %s AND user_id = %s
        RETURNING session_id, user_id, title, created_at, updated_at
        """,
        (title, _now(), session_id, user_id),
    ).fetchone()
    return _row_to_session(row) if row is not None else None


def delete_chat_session(
    connection: Connection,
    user_id: str,
    session_id: str,
) -> bool:
    cursor = connection.execute(
        "DELETE FROM chat_sessions WHERE session_id = %s AND user_id = %s",
        (session_id, user_id),
    )
    return cursor.rowcount > 0


def append_message(
    connection: Connection,
    *,
    session_id: str,
    user_id: str,
    role: str,
    content: str,
    task_type: str = "",
    run_id: str = "",
    result_json: dict[str, Any] | None = None,
) -> str:
    if role not in {"user", "assistant"}:
        raise ValueError(f"invalid message role: {role}")
    message_id = str(uuid4())
    timestamp = _now()
    connection.execute(
        """
        INSERT INTO chat_messages(
            message_id, session_id, user_id, role, content,
            task_type, run_id, result_json, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            message_id,
            session_id,
            user_id,
            role,
            content,
            task_type,
            run_id,
            json.dumps(result_json, ensure_ascii=False) if result_json is not None else None,
            timestamp,
        ),
    )
    connection.execute(
        "UPDATE chat_sessions SET updated_at = %s WHERE session_id = %s",
        (timestamp, session_id),
    )
    return message_id


def _row_to_message(row: Row) -> dict[str, Any]:
    message = {
        "message_id": row["message_id"],
        "session_id": row["session_id"],
        "user_id": row["user_id"],
        "role": row["role"],
        "content": row["content"],
        "task_type": row["task_type"],
        "run_id": row["run_id"],
        "created_at": row["created_at"],
    }
    raw = row["result_json"]
    if raw:
        try:
            message["result"] = json.loads(raw)
        except json.JSONDecodeError:
            message["result"] = None
    else:
        message["result"] = None
    return message


def list_messages(
    connection: Connection,
    session_id: str,
    limit: int = 200,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT message_id, session_id, user_id, role, content,
               task_type, run_id, result_json, created_at
        FROM chat_messages
        WHERE session_id = %s
        ORDER BY created_at ASC, message_seq ASC
        LIMIT %s
        """,
        (session_id, limit),
    ).fetchall()
    return [_row_to_message(row) for row in rows]


def active_outfit_messages(
    connection: Connection,
    user_id: str,
    session_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return assistant messages that produced an outfit, newest first."""
    placeholders = ",".join("%s" for _ in OUTFIT_TASK_TYPES)
    rows = connection.execute(
        f"""
        SELECT message_id, session_id, user_id, role, content,
               task_type, run_id, result_json, created_at
        FROM chat_messages
        WHERE session_id = %s AND user_id = %s AND role = 'assistant'
          AND task_type IN ({placeholders}) AND result_json IS NOT NULL
        ORDER BY created_at DESC, message_seq DESC
        LIMIT %s
        """,  # noqa: S608
        (session_id, user_id, *OUTFIT_TASK_TYPES, limit),
    ).fetchall()
    return [_row_to_message(row) for row in rows]
