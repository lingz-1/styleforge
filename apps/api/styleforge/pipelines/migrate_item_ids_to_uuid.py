"""One-time migration: re-id every catalog item to a random UUID.

The catalog used dataset ids (e.g. ``P00893230``, ``100863944_4``) as
``catalog_items.item_id``. This script mints a fresh UUID per row, keeps the old
id in ``dataset_item_id`` (empty for personal items), and rewires every
referencing table in a single transaction. Re-running is a no-op once all
dataset rows carry a ``dataset_item_id``.

Run with the style env. Usage:
  D:\\anaconda\\envs\\style\\python.exe -m styleforge.pipelines.migrate_item_ids_to_uuid --database <dsn>
"""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone

from styleforge.core.config import Settings
from styleforge.repositories.database import database_session, initialize_database

# (table, column) pairs referencing catalog_items(item_id) that can be re-pointed
# with a plain UPDATE once the new UUID parent rows exist.
_CATALOG_CHILD_TABLES = [
    ("catalog_item_images", "item_id"),
    ("dataset_outfit_items", "item_id"),
    ("wardrobe_items", "item_id"),
]

_CATALOG_COLUMNS = (
    "source, gender, item_type, main_category, name, color, description, "
    "features_json, attributes_json, image_filename, relative_image_path, "
    "image_status, embedding_status, raw_json_hash, source_revision, imported_at"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def migrate_item_ids_to_uuid(database_path: str) -> dict[str, object]:
    """Re-id every catalog item to a random UUID. Idempotent; single transaction."""
    initialize_database(database_path)  # also adds dataset_item_id idempotently
    with database_session(database_path) as connection:
        pending = connection.execute(
            "SELECT COUNT(*) FROM catalog_items "
            "WHERE source IN ('mytheresa', 'polyvore') AND dataset_item_id = ''"
        ).fetchone()[0]
        if pending == 0:
            return {
                "status": "already_migrated",
                "generated_at": _now(),
                "migrated_items": 0,
            }

        rows = connection.execute(
            f"SELECT item_id, {_CATALOG_COLUMNS} FROM catalog_items"
        ).fetchall()

        # Build old -> new id mapping and stage it in a temp table so the SQL
        # below can resolve references on the server.
        connection.execute(
            "CREATE TEMP TABLE old_new(old_id TEXT PRIMARY KEY, new_id TEXT NOT NULL)"
        )
        mapping: list[tuple[str, str]] = [(row["item_id"], str(uuid.uuid4())) for row in rows]
        connection.executemany(
            "INSERT INTO old_new(old_id, new_id) VALUES (%s, %s)",
            mapping,
        )

        # Insert the new parent rows first so child tables can point at them.
        connection.execute(
            f"""
            INSERT INTO catalog_items (item_id, dataset_item_id, {_CATALOG_COLUMNS})
            SELECT m.new_id,
                   CASE WHEN c.item_id LIKE 'personal:%' THEN '' ELSE c.item_id END,
                   c.source, c.gender, c.item_type, c.main_category, c.name,
                   c.color, c.description, c.features_json, c.attributes_json,
                   c.image_filename, c.relative_image_path, c.image_status,
                   c.embedding_status, c.raw_json_hash, c.source_revision,
                   c.imported_at
            FROM catalog_items c
            JOIN old_new m ON m.old_id = c.item_id
            """
        )

        # Rewire tables that reference catalog_items directly. All new parent
        # rows already exist, so no RESTRICT / SET NULL / CASCADE blocks them.
        for table, column in _CATALOG_CHILD_TABLES:
            connection.execute(
                f"UPDATE {table} SET {column} = m.new_id "
                f"FROM old_new m WHERE {column} = m.old_id"  # noqa: S608
            )
        connection.execute(
            "UPDATE wardrobe_import_rows SET catalog_item_id = m.new_id "
            "FROM old_new m WHERE catalog_item_id = m.old_id"
        )
        # personal_wardrobe_items is itself referenced by personal_item_embeddings,
        # so it cannot be re-pointed in place: insert new UUID rows first, rewire
        # the deeper child, then drop the old rows.
        connection.execute(
            """
            INSERT INTO personal_wardrobe_items (
                item_id, user_id, import_row_id, external_order_id_hash,
                order_submitted_at, order_status, shop_name, external_product_id,
                variant_text, quantity_owned, listed_amount, paid_amount, currency,
                canonical_url, ownership_status, review_status, created_at, updated_at
            )
            SELECT m.new_id, p.user_id, p.import_row_id, p.external_order_id_hash,
                   p.order_submitted_at, p.order_status, p.shop_name,
                   p.external_product_id, p.variant_text, p.quantity_owned,
                   p.listed_amount, p.paid_amount, p.currency, p.canonical_url,
                   p.ownership_status, p.review_status, p.created_at, p.updated_at
            FROM personal_wardrobe_items p
            JOIN old_new m ON m.old_id = p.item_id
            """
        )
        connection.execute(
            "UPDATE personal_item_embeddings SET item_id = m.new_id "
            "FROM old_new m WHERE item_id = m.old_id"
        )
        connection.execute(
            "DELETE FROM personal_wardrobe_items "
            "WHERE item_id IN (SELECT old_id FROM old_new)"
        )

        # Remap stale current-item ids embedded in historical candidate outfits
        # so future outfit_modify runs against them still validate.
        old_to_new = dict(mapping)
        outfit_rows = connection.execute(
            "SELECT run_id, outfit_id, item_ids_json FROM candidate_outfits"
        ).fetchall()
        outfit_updates = 0
        for row in outfit_rows:
            try:
                item_ids = json.loads(row["item_ids_json"])
            except (ValueError, TypeError):
                continue
            remapped = [old_to_new.get(item_id, item_id) for item_id in item_ids]
            if remapped != item_ids:
                connection.execute(
                    "UPDATE candidate_outfits SET item_ids_json = %s "
                    "WHERE run_id = %s AND outfit_id = %s",
                    (json.dumps(remapped, ensure_ascii=False), row["run_id"], row["outfit_id"]),
                )
                outfit_updates += 1

        # Drop the old rows; every referencing row has moved to the new ids.
        connection.execute(
            "DELETE FROM catalog_items WHERE item_id IN (SELECT old_id FROM old_new)"
        )
        connection.execute("ANALYZE catalog_items")

        # Post-migration self-check.
        orphan_wardrobe = connection.execute(
            "SELECT COUNT(*) FROM wardrobe_items w "
            "LEFT JOIN catalog_items c ON w.item_id = c.item_id "
            "WHERE c.item_id IS NULL"
        ).fetchone()[0]
        empty_dataset_ids = connection.execute(
            "SELECT COUNT(*) FROM catalog_items "
            "WHERE source IN ('mytheresa', 'polyvore') AND dataset_item_id = ''"
        ).fetchone()[0]

    return {
        "status": "migrated",
        "generated_at": _now(),
        "migrated_items": len(mapping),
        "candidate_outfit_json_updates": outfit_updates,
        "orphan_wardrobe_items": orphan_wardrobe,
        "dataset_rows_without_dataset_id": empty_dataset_ids,
    }


def build_parser() -> argparse.ArgumentParser:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=str, default=settings.database_dsn)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = migrate_item_ids_to_uuid(args.database)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
