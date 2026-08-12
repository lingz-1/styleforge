from __future__ import annotations

from styleforge.repositories.chat_repository import (
    append_message,
    create_chat_session,
    delete_chat_session,
    get_chat_session,
    list_chat_sessions,
    list_messages,
    rename_chat_session,
)
from styleforge.repositories.database import database_session, initialize_database
from styleforge.services.chat_service import get_session_outfit_context


def test_chat_session_crud_and_cascade_delete(db_dsn: str) -> None:
    database_path = db_dsn
    initialize_database(database_path)
    with database_session(database_path) as connection:
        session = create_chat_session(connection, user_id="u", title="通勤讨论")
        session_id = session["session_id"]
        assert get_chat_session(connection, "u", session_id)["title"] == "通勤讨论"
        assert get_chat_session(connection, "other", session_id) is None

        append_message(
            connection,
            session_id=session_id,
            user_id="u",
            role="user",
            content="推荐一套",
        )
        append_message(
            connection,
            session_id=session_id,
            user_id="u",
            role="assistant",
            content="好的",
            task_type="outfit_recommend",
            run_id="run-1",
            result_json={"task_type": "outfit_recommend", "result": {"status": "completed"}},
        )
        renamed = rename_chat_session(connection, "u", session_id, "新标题")
        assert renamed["title"] == "新标题"

        sessions = list_chat_sessions(connection, "u")
        assert sessions[0]["session_id"] == session_id
        assert sessions[0]["last_message"]["content"] == "好的"

        messages = list_messages(connection, session_id)
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[1]["result"]["result"]["status"] == "completed"

        assert delete_chat_session(connection, "u", session_id)
        assert list_messages(connection, session_id) == []


def test_get_session_outfit_context_uses_most_recent_outfit(db_dsn: str) -> None:
    database_path = db_dsn
    initialize_database(database_path)
    with database_session(database_path) as connection:
        session = create_chat_session(connection, user_id="u")
        session_id = session["session_id"]
        append_message(
            connection,
            session_id=session_id,
            user_id="u",
            role="assistant",
            content="第一套",
            task_type="outfit_recommend",
            result_json={
                "task_type": "outfit_recommend",
                "result": {
                    "structured_result": {
                        "recommendations": [
                            {"outfit_id": "old", "item_ids": ["a", "b"]}
                        ]
                    }
                },
            },
        )
        append_message(
            connection,
            session_id=session_id,
            user_id="u",
            role="assistant",
            content="第二套",
            task_type="outfit_modify",
            result_json={
                "task_type": "outfit_modify",
                "result": {
                    "current_outfit_id": "new",
                    "alternatives": [{"item_ids": ["c", "d", "e"]}],
                },
            },
        )
        append_message(
            connection,
            session_id=session_id,
            user_id="u",
            role="assistant",
            content="风格建议",
            task_type="style_advice",
            result_json={"task_type": "style_advice", "result": {"summary": "..."}},
        )
        context = get_session_outfit_context(connection, "u", session_id)
    assert context["current_outfit_id"] == "new"
    assert context["current_item_ids"] == ["c", "d", "e"]


def test_get_session_outfit_context_ignores_empty_turns(db_dsn: str) -> None:
    database_path = db_dsn
    initialize_database(database_path)
    with database_session(database_path) as connection:
        session = create_chat_session(connection, user_id="u")
        session_id = session["session_id"]
        append_message(
            connection,
            session_id=session_id,
            user_id="u",
            role="assistant",
            content="没有搭配",
            task_type="outfit_recommend",
            result_json={"task_type": "outfit_recommend", "result": {}},
        )
        context = get_session_outfit_context(connection, "u", session_id)
    assert context == {"current_outfit_id": "", "current_item_ids": []}
