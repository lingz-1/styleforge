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
