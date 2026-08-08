from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.request_memory_repository import (
    list_structure_signatures,
    recent_request_memories,
    save_request_memory,
)


def _db(tmp_path):
    path = tmp_path / "memory.sqlite"
    initialize_database(path)
    return path


def test_save_and_read_back_memory(tmp_path) -> None:
    path = _db(tmp_path)
    signature = {"theme": "面试", "unique_mood": ["专业"]}
    with database_session(path) as connection:
        save_request_memory(connection, user_id="u", request_signature=signature)

        memories = recent_request_memories(connection, "u")

    assert len(memories) == 1
    assert memories[0]["request_signature"] == signature


def test_keeps_only_max_recent_entries(tmp_path) -> None:
    path = _db(tmp_path)
    with database_session(path) as connection:
        for index in range(8):
            save_request_memory(
                connection,
                user_id="u",
                request_signature={"theme": f"request-{index}"},
                max_recent=5,
            )

        memories = recent_request_memories(connection, "u", limit=10)

    assert len(memories) == 5
    themes = [memory["request_signature"]["theme"] for memory in memories]
    # Most recent five survive (reverse chronological).
    assert "request-7" in themes
    assert "request-0" not in themes


def test_memory_scoped_by_user(tmp_path) -> None:
    path = _db(tmp_path)
    with database_session(path) as connection:
        save_request_memory(connection, user_id="a", request_signature={"theme": "x"})
        save_request_memory(connection, user_id="b", request_signature={"theme": "y"})

        memories_a = recent_request_memories(connection, "a")
        memories_b = recent_request_memories(connection, "b")

    assert len(memories_a) == 1
    assert memories_a[0]["request_signature"]["theme"] == "x"
    assert memories_b[0]["request_signature"]["theme"] == "y"


def test_structure_signatures_round_trip(tmp_path) -> None:
    path = _db(tmp_path)
    structure = {
        "category_structure": ["top", "bottom"],
        "dominant_color_family": ["dark_neutral"],
        "style_mix": ["formal"],
        "layer_count": 1,
    }
    with database_session(path) as connection:
        save_request_memory(
            connection,
            user_id="u",
            request_signature={"theme": "面试"},
            structure_signature=structure,
        )
        save_request_memory(
            connection,
            user_id="u",
            request_signature={"theme": "约会"},
            structure_signature=None,
        )

        signatures = list_structure_signatures(connection, "u")

    assert len(signatures) == 1
    assert signatures[0]["dominant_color_family"] == ["dark_neutral"]


def test_save_requires_user_id(tmp_path) -> None:
    path = _db(tmp_path)
    with database_session(path) as connection:
        try:
            save_request_memory(connection, user_id="", request_signature={"theme": "x"})
            raised = False
        except ValueError:
            raised = True
    assert raised
