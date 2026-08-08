"""Import audited Garments2Look-Polyvore outfit relationships into SQLite."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from styleforge.common.files import write_json_atomic
from styleforge.core.config import Settings
from styleforge.data.json_stream import iter_json_object
from styleforge.data.outfits import normalize_outfit
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.import_run_repository import (
    fail_import_run,
    finish_import_run,
    start_import_run,
    update_progress,
)
from styleforge.repositories.outfit_repository import outfit_counts, upsert_outfits


def _catalog_item_ids(database_path: Path) -> set[str]:
    with database_session(database_path) as connection:
        return {row[0] for row in connection.execute("SELECT item_id FROM catalog_items")}


def import_outfits(
    outfit_path: Path,
    database_path: Path,
    source_revision: str,
    batch_size: int = 500,
) -> dict[str, object]:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    initialize_database(database_path)
    catalog_item_ids = _catalog_item_ids(database_path)
    if not catalog_item_ids:
        raise ValueError("Catalog is empty; import item metadata before outfit relationships")
    with database_session(database_path) as connection:
        run_id = start_import_run(
            connection,
            outfit_path,
            source_revision,
            None,
            dataset_name="Garments2Look-Polyvore-Outfits",
        )

    processed_count = 0
    relation_count = 0
    skipped_incomplete_outfits = 0
    skipped_orphan_relations = 0
    try:
        batch = []
        for outfit_id, record in iter_json_object(outfit_path):
            outfit = normalize_outfit(outfit_id, record)
            orphan_count = sum(
                1 for item_id, _ in outfit.items if item_id not in catalog_item_ids
            )
            if orphan_count:
                skipped_incomplete_outfits += 1
                skipped_orphan_relations += orphan_count
                continue
            batch.append(outfit)
            if len(batch) >= batch_size:
                with database_session(database_path) as connection:
                    imported, relations = upsert_outfits(connection, batch, source_revision)
                    processed_count += imported
                    relation_count += relations
                    update_progress(connection, run_id, processed_count)
                batch.clear()
        if batch:
            with database_session(database_path) as connection:
                imported, relations = upsert_outfits(connection, batch, source_revision)
                processed_count += imported
                relation_count += relations
                update_progress(connection, run_id, processed_count)
        with database_session(database_path) as connection:
            finish_import_run(connection, run_id, processed_count)
            counts = outfit_counts(connection)
    except BaseException as error:
        with database_session(database_path) as connection:
            fail_import_run(connection, run_id, error)
        raise

    return {
        "schema_version": "styleforge.outfit-import.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "status": "completed",
        "source_path": str(outfit_path.resolve()),
        "source_revision": source_revision,
        "processed_outfits": processed_count,
        "processed_relations": relation_count,
        "skipped_incomplete_outfits": skipped_incomplete_outfits,
        "skipped_orphan_relations": skipped_orphan_relations,
        "coverage_policy": "strict_all_items_must_exist_in_catalog",
        "database_path": str(database_path.resolve()),
        "database_counts": counts,
    }


def build_parser() -> argparse.ArgumentParser:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outfits", type=Path, default=settings.outfit_path)
    parser.add_argument("--database", type=Path, default=settings.database_path)
    parser.add_argument("--source-revision", default=settings.dataset_revision)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument(
        "--report",
        type=Path,
        default=settings.artifact_root / "imports" / "polyvore_outfit_import.json",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not args.outfits.is_file():
        raise SystemExit(f"Outfit file not found: {args.outfits}")
    report = import_outfits(
        outfit_path=args.outfits,
        database_path=args.database,
        source_revision=args.source_revision,
        batch_size=args.batch_size,
    )
    write_json_atomic(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
