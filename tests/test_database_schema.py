import sqlite3

import pytest

from styleforge.repositories.database import SCHEMA_VERSION, initialize_database


def test_initialize_rejects_a_database_from_a_newer_schema(tmp_path) -> None:
    database_path = tmp_path / "future.db"
    connection = sqlite3.connect(database_path)
    connection.execute(
        "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION + 1),),
    )
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError, match="newer than this StyleForge build"):
        initialize_database(database_path)


def test_schema_contains_personal_wardrobe_import_tables(tmp_path) -> None:
    database_path = tmp_path / "styleforge.db"
    initialize_database(database_path)
    connection = sqlite3.connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        import_row_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(wardrobe_import_rows)")
        }
        personal_item_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(personal_wardrobe_items)")
        }
    finally:
        connection.close()

    assert version == str(SCHEMA_VERSION)
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


def test_schema_v8_migrates_task_runs_status_without_losing_rows(tmp_path) -> None:
    database_path = tmp_path / "schema-v7.db"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO schema_meta(key, value) VALUES('schema_version', '7');
        CREATE TABLE task_runs (
            run_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            task_type TEXT NOT NULL,
            request TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('running', 'completed', 'infeasible', 'failed')
            ),
            context_pack_json TEXT NOT NULL DEFAULT '{}',
            result_json TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            finished_at TEXT
        );
        INSERT INTO task_runs(
            run_id, user_id, task_type, request, status, created_at
        ) VALUES ('old-run', 'u', 'item_advice', '旧记录', 'completed', '2026-08-09');
        """
    )
    connection.commit()
    connection.close()

    initialize_database(database_path)

    connection = sqlite3.connect(database_path)
    try:
        old_row = connection.execute(
            "SELECT user_id, status FROM task_runs WHERE run_id = 'old-run'"
        ).fetchone()
        connection.execute(
            """
            INSERT INTO task_runs(
                run_id, user_id, task_type, request, status, created_at
            ) VALUES ('clarify-run', 'u', 'item_advice', '待补充',
                      'needs_clarification', '2026-08-10')
            """
        )
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
    finally:
        connection.close()

    assert old_row == ("u", "completed")
    assert version == str(SCHEMA_VERSION)
