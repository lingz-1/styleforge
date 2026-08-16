"""Migration tests: re-id every catalog item to a random UUID.

The migration rewires every referencing table in a single transaction and is
idempotent. These tests seed a small multi-source catalog (dataset ids plus one
personal item) together with child rows and a historical candidate outfit, then
assert every id becomes a UUID, ``dataset_item_id`` preserves the raw ids, and
all references follow.
"""

from __future__ import annotations

import json
import re

from styleforge.pipelines.migrate_item_ids_to_uuid import migrate_item_ids_to_uuid
from styleforge.repositories.catalog_repository import upsert_items
from styleforge.repositories.database import connect, database_session, initialize_database

from tests.helpers import make_item

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _seed(db_dsn: str) -> None:
    initialize_database(db_dsn)
    items = [
        make_item("P00893230", "activewear_bra", "Tank top", "white", source="mytheresa"),
        make_item("100863944_4", "bag", "Leopard bag", "leopard", source="polyvore"),
        make_item("personal:probe-uuid", "top", "Personal shirt", "blue", source="personal-user"),
    ]
    with database_session(db_dsn) as connection:
        upsert_items(connection, items, "test")
        # child rows referencing the dataset item
        connection.execute(
            "INSERT INTO catalog_item_images (item_id, position, image_role, image_filename, "
            "relative_image_path, image_status, is_primary) "
            "VALUES ('P00893230', 0, 'primary', 'a.jpg', 'P00893230/a.jpg', 'available', 1)"
        )
        connection.execute(
            "INSERT INTO wardrobe_items (user_id, item_id, active, favorite, notes, added_at) "
            "VALUES ('u1', 'P00893230', 1, 0, '', '2026-01-01T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO wardrobe_items (user_id, item_id, active, favorite, notes, added_at) "
            "VALUES ('u1', 'personal:probe-uuid', 1, 0, '', '2026-01-01T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO dataset_outfits (outfit_id, source, source_revision, imported_at) "
            "VALUES ('outfit-1', 'polyvore', 'rev', '2026-01-01T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO dataset_outfit_items (outfit_id, position, item_id) "
            "VALUES ('outfit-1', 0, 'P00893230')"
        )
        connection.execute(
            "INSERT INTO wardrobe_import_batches (batch_id, user_id, platform, source_filename, "
            "file_sha256, status, created_at) "
            "VALUES ('batch-1', 'u1', 'taobao', 'a.csv', 'abc', 'previewed', '2026-01-01T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO wardrobe_import_rows (row_id, batch_id, source_row_number, row_hash, "
            "product_name, decision, catalog_item_id) "
            "VALUES ('row-1', 'batch-1', 1, 'h', 'Tank top', 'committed', 'P00893230')"
        )
        connection.execute(
            "INSERT INTO personal_wardrobe_items (item_id, user_id, ownership_status, "
            "review_status, created_at, updated_at) "
            "VALUES ('personal:probe-uuid', 'u1', 'owned', 'confirmed', "
            "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO personal_item_embeddings (item_id, embedding_kind, dimension, "
            "vector_blob, model_revision, embedded_at) "
            "VALUES ('personal:probe-uuid', 'text', 3, '\\\\x010203'::bytea, 'r1', '2026-01-01T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO styling_runs (run_id, user_id, status, task_spec_json, created_at) "
            "VALUES ('run-1', 'u1', 'completed', '{}', '2026-01-01T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO candidate_outfits (run_id, outfit_id, rank, score, hard_valid, "
            "item_ids_json, score_details_json) "
            "VALUES ('run-1', 'outfit-1', 1, 1.0, 1, "
            "json_build_array('P00893230', '100863944_4')::text, '{}')"
        )


def _all_catalog(db_dsn: str) -> list[dict]:
    with connect(db_dsn) as connection:
        rows = connection.execute("SELECT item_id, dataset_item_id, source FROM catalog_items")
        return [dict(row) for row in rows]


def test_migration_rewrites_all_ids_to_uuids(db_dsn: str) -> None:
    _seed(db_dsn)
    report = migrate_item_ids_to_uuid(db_dsn)

    assert report["status"] == "migrated"
    assert report["migrated_items"] == 3

    rows = _all_catalog(db_dsn)
    assert {r["dataset_item_id"] for r in rows} == {"P00893230", "100863944_4", ""}
    for row in rows:
        assert UUID_RE.match(row["item_id"]), row
        # personal items keep an empty dataset_item_id.
        if row["source"].startswith("personal"):
            assert row["dataset_item_id"] == ""

    with connect(db_dsn) as connection:
        # every referencing table now points at a UUID that exists in catalog
        refs = {
            "catalog_item_images": "SELECT item_id FROM catalog_item_images",
            "wardrobe_items": "SELECT item_id FROM wardrobe_items",
            "dataset_outfit_items": "SELECT item_id FROM dataset_outfit_items",
            "personal_wardrobe_items": "SELECT item_id FROM personal_wardrobe_items",
            "personal_item_embeddings": "SELECT item_id FROM personal_item_embeddings",
            "wardrobe_import_rows": "SELECT catalog_item_id FROM wardrobe_import_rows",
        }
        for label, sql in refs.items():
            for row in connection.execute(sql):
                value = row[0]
                assert value is None or UUID_RE.match(value), (label, value)
                if value is not None:
                    exists = connection.execute(
                        "SELECT 1 FROM catalog_items WHERE item_id = %s", (value,)
                    ).fetchone()
                    assert exists, (label, value)

        # historical candidate outfit json remapped to the new ids
        outfit = connection.execute(
            "SELECT item_ids_json FROM candidate_outfits WHERE run_id = 'run-1'"
        ).fetchone()
        ids = json.loads(outfit["item_ids_json"])
        assert len(ids) == 2
        for item_id in ids:
            assert UUID_RE.match(item_id)

    # idempotent: a second run is a no-op
    second = migrate_item_ids_to_uuid(db_dsn)
    assert second["status"] == "already_migrated"
    assert _all_catalog(db_dsn) == rows


def test_migration_on_empty_db_is_noop(db_dsn: str) -> None:
    initialize_database(db_dsn)
    report = migrate_item_ids_to_uuid(db_dsn)
    assert report["status"] == "already_migrated"
