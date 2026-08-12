from __future__ import annotations

from pathlib import Path

import pytest

from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.memory_repository import (
    active_memory_profile,
    create_manual_memory,
    forget_memory,
    list_memories,
    update_memory,
    upsert_auto_memories,
)


def test_auto_confidence_accumulates_in_steps(tmp_path: Path) -> None:
    database_path = tmp_path / "memories.db"
    initialize_database(database_path)
    extract = {"category": "color", "content": "黑色", "meta": {"polarity": "positive"}}
    expected = [0.35, 0.6, 0.85, 1.0]
    for index, value in enumerate(expected, start=1):
        with database_session(database_path) as connection:
            upsert_auto_memories(connection, "u", [extract])
            profile = active_memory_profile(connection, "u")
        assert len(profile) == 1
        assert profile[0]["occurrences"] == index
        assert profile[0]["confidence"] == pytest.approx(value)
    # Further observations stay capped.
    with database_session(database_path) as connection:
        upsert_auto_memories(connection, "u", [extract])
        profile = active_memory_profile(connection, "u")
    assert profile[0]["confidence"] == 1.0


def test_manual_memory_wins_over_auto(tmp_path: Path) -> None:
    database_path = tmp_path / "memories.db"
    initialize_database(database_path)
    extract = {"category": "color", "content": "黑色", "meta": {}}
    with database_session(database_path) as connection:
        upsert_auto_memories(connection, "u", [extract])
        create_manual_memory(connection, "u", "color", "黑色", confidence=0.9)
        upsert_auto_memories(connection, "u", [extract])
    with database_session(database_path) as connection:
        memory = active_memory_profile(connection, "u")[0]
    assert memory["source"] == "manual"
    assert memory["confidence"] == 0.9
    assert memory["occurrences"] == 0


def test_update_memory_edits_fields(tmp_path: Path) -> None:
    database_path = tmp_path / "memories.db"
    initialize_database(database_path)
    with database_session(database_path) as connection:
        memory = create_manual_memory(connection, "u", "formality", "正式", confidence=0.8)
        updated = update_memory(
            connection, "u", memory["memory_id"], content="商务", confidence=0.5
        )
        assert updated["content"] == "商务"
        assert updated["confidence"] == 0.5
        assert update_memory(connection, "u", 99999, content="x") is None


def test_forget_revives_auto_memory(tmp_path: Path) -> None:
    database_path = tmp_path / "memories.db"
    initialize_database(database_path)
    extract = {"category": "formality", "content": "正式", "meta": {}}
    with database_session(database_path) as connection:
        upsert_auto_memories(connection, "u", [extract])
        memory = list_memories(connection, "u")[0]
        assert forget_memory(connection, "u", memory["memory_id"])
        assert active_memory_profile(connection, "u") == []
        processed = upsert_auto_memories(connection, "u", [extract])
    assert processed == 1
    with database_session(database_path) as connection:
        active = active_memory_profile(connection, "u")
    assert len(active) == 1
    assert active[0]["active"] is True
    assert active[0]["occurrences"] == 2
    assert active[0]["confidence"] == pytest.approx(0.6)


def test_create_manual_memory_validates_inputs(tmp_path: Path) -> None:
    database_path = tmp_path / "memories.db"
    initialize_database(database_path)
    with database_session(database_path) as connection:
        with pytest.raises(ValueError, match="invalid memory category"):
            create_manual_memory(connection, "u", "nope", "x")
        with pytest.raises(ValueError, match="confidence"):
            create_manual_memory(connection, "u", "color", "黑色", confidence=2.0)
