from __future__ import annotations

from pathlib import Path

from styleforge.context.builder import ContextPackBuilder
from styleforge.models.task import TaskExecutionInput
from styleforge.orchestration.task_router import TaskRouter
from styleforge.repositories.catalog_repository import upsert_items
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.memory_repository import create_manual_memory
from styleforge.repositories.wardrobe_repository import add_items

from tests.helpers import make_item


def test_context_pack_contains_request_wardrobe_and_user_context(tmp_path: Path) -> None:
    database_path = tmp_path / "styleforge.db"
    initialize_database(database_path)
    items = [
        make_item("top-1", "top", "White shirt", "white"),
        make_item("shoe-1", "shoes", "Black loafers", "black"),
    ]
    with database_session(database_path) as connection:
        upsert_items(connection, items, "test")
        add_items(connection, "u", [item.item_id for item in items])
    task_input = TaskExecutionInput(user_id="u", request="American Vintage 怎么穿？")
    route = TaskRouter().route(task_input.request)

    pack = ContextPackBuilder(database_path).build(task_input, route)

    assert pack.request_context.task_type == "style_advice"
    assert pack.user_context.wardrobe_summary["item_count"] == 2
    assert pack.user_context.wardrobe_summary["slot_counts"] == {
        "footwear": 1,
        "top": 1,
    }
    assert len(pack.user_context.preferences["evaluation_profile"]["weights"]) == 5


def test_context_pack_injects_memory_profile(tmp_path: Path) -> None:
    database_path = tmp_path / "mem-ctx.db"
    initialize_database(database_path)
    with database_session(database_path) as connection:
        create_manual_memory(connection, "u", "formality", "正式", confidence=0.9)
    task_input = TaskExecutionInput(user_id="u", request="美国复古风怎么穿？")
    route = TaskRouter().route(task_input.request)

    pack = ContextPackBuilder(database_path).build(task_input, route)

    profile = pack.user_context.preferences.get("memory_profile", [])
    assert len(profile) == 1
    assert profile[0]["content"] == "正式"
    assert profile[0]["source"] == "manual"


def test_context_pack_omits_memory_profile_when_empty(tmp_path: Path) -> None:
    database_path = tmp_path / "mem-ctx.db"
    initialize_database(database_path)
    task_input = TaskExecutionInput(user_id="u", request="美国复古风怎么穿？")
    route = TaskRouter().route(task_input.request)

    pack = ContextPackBuilder(database_path).build(task_input, route)

    assert "memory_profile" not in pack.user_context.preferences
