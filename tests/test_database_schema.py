"""PostgreSQL schema tests backed by ``information_schema``.

Each test gets its own isolated schema via the ``db_dsn`` fixture, so table
existence is asserted against ``current_schema()`` rather than ``sqlite_master``.
"""

from __future__ import annotations

import pytest

from styleforge.repositories.database import SCHEMA_VERSION, connect, initialize_database


def _table_names(dsn: str) -> set[str]:
    with connect(dsn) as connection:
        return {
            row["table_name"]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema()"
            )
        }


def _column_names(dsn: str, table: str) -> set[str]:
    with connect(dsn) as connection:
        return {
            row["column_name"]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = %s",
                (table,),
            )
        }


def _schema_version(dsn: str) -> str:
    with connect(dsn) as connection:
        row = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
    assert row is not None
    return row["value"]


def test_initialize_rejects_a_database_from_a_newer_schema(db_dsn: str) -> None:
    with connect(db_dsn) as connection:
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', %s)",
            (str(SCHEMA_VERSION + 1),),
        )
        connection.commit()

    with pytest.raises(RuntimeError, match="newer than this StyleForge build"):
        initialize_database(db_dsn)


def test_schema_contains_personal_wardrobe_import_tables(db_dsn: str) -> None:
    initialize_database(db_dsn)
    tables = _table_names(db_dsn)
    import_row_columns = _column_names(db_dsn, "wardrobe_import_rows")
    personal_item_columns = _column_names(db_dsn, "personal_wardrobe_items")

    assert _schema_version(db_dsn) == str(SCHEMA_VERSION)
    assert {
        "wardrobe_import_batches",
        "wardrobe_import_rows",
        "personal_wardrobe_items",
        "personal_item_embeddings",
        "task_runs",
    }.issubset(tables)
    assert {
        "external_order_id_hash",
        "order_status",
        "refund_status",
        "after_sale_status",
        "logistics_status",
        "order_eligibility",
    }.issubset(import_row_columns)
    assert {
        "external_order_id_hash",
        "order_submitted_at",
        "order_status",
        "shop_name",
    }.issubset(personal_item_columns)


def test_schema_v11_contains_chat_and_preference_memory_tables(db_dsn: str) -> None:
    initialize_database(db_dsn)
    with connect(db_dsn) as connection:
        tables = _table_names(db_dsn)
        indexes = {
            row["indexname"]
            for row in connection.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()"
            )
        }
        message_columns = _column_names(db_dsn, "chat_messages")
        event_columns = _column_names(db_dsn, "interaction_events")
        evidence_columns = _column_names(db_dsn, "preference_evidence")
        preference_columns = _column_names(db_dsn, "preference_model")

    assert SCHEMA_VERSION == 11
    assert {"chat_sessions", "chat_messages"}.issubset(tables)
    # The retired user_memories table is gone, replaced by the evidence + model pair.
    assert "user_memories" not in tables
    assert {"interaction_events", "preference_evidence", "preference_model"}.issubset(
        tables
    )
    assert {"idx_chat_sessions_user_updated", "idx_chat_messages_session_created"}.issubset(
        indexes
    )
    assert {"result_json", "task_type", "run_id"}.issubset(message_columns)
    assert {"event_type", "context_json", "features_json"}.issubset(event_columns)
    assert {"polarity", "strength", "scope_json", "source"}.issubset(evidence_columns)
    assert {
        "dimension",
        "attribute",
        "value",
        "lifecycle",
        "support_count",
        "contradiction_count",
    }.issubset(preference_columns)


def test_initialize_database_is_idempotent(db_dsn: str) -> None:
    initialize_database(db_dsn)
    initialize_database(db_dsn)
    assert _schema_version(db_dsn) == str(SCHEMA_VERSION)
    assert {"catalog_items", "chat_messages", "task_runs"}.issubset(_table_names(db_dsn))
