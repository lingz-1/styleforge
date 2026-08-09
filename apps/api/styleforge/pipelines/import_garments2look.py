"""Import Garments2Look-Polyvore metadata into the local SQLite catalog."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from styleforge.common.files import write_json_atomic
from styleforge.core.config import Settings
from styleforge.data.garments2look import ImagePathResolver, normalize_catalog_item
from styleforge.data.json_stream import iter_json_object
from styleforge.repositories.catalog_repository import catalog_counts, upsert_items
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.dataset_source_repository import register_dataset_source
from styleforge.repositories.import_run_repository import (
    fail_import_run,
    finish_import_run,
    start_import_run,
    update_progress,
)


def import_metadata(
    metadata_path: Path,
    database_path: Path,
    image_root: Path | None,
    source_revision: str,
    batch_size: int = 1000,
) -> dict[str, object]:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    initialize_database(database_path)
    resolver = ImagePathResolver(image_root)

    with database_session(database_path) as connection:
        if image_root is not None:
            register_dataset_source(
                connection,
                source="polyvore",
                image_root=image_root,
                metadata_path=metadata_path,
                source_revision=source_revision,
            )
        run_id = start_import_run(connection, metadata_path, source_revision, image_root)
    processed_count = 0

    try:
        batch = []
        for item_id, record in iter_json_object(metadata_path):
            batch.append(normalize_catalog_item(item_id, record, resolver))
            if len(batch) >= batch_size:
                with database_session(database_path) as connection:
                    processed_count += upsert_items(connection, batch, source_revision)
                    update_progress(connection, run_id, processed_count)
                batch.clear()

        if batch:
            with database_session(database_path) as connection:
                processed_count += upsert_items(connection, batch, source_revision)
                update_progress(connection, run_id, processed_count)

        with database_session(database_path) as connection:
            finish_import_run(connection, run_id, processed_count)
            counts = catalog_counts(connection)
    except BaseException as error:
        with database_session(database_path) as connection:
            fail_import_run(connection, run_id, error)
        raise

    return {
        "schema_version": "styleforge.metadata-import.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "status": "completed",
        "source_path": str(metadata_path.resolve()),
        "source_revision": source_revision,
        "image_root": str(image_root.resolve()) if image_root is not None else None,
        "processed_count": processed_count,
        "database_path": str(database_path.resolve()),
        "database_counts": counts,
    }


def build_parser() -> argparse.ArgumentParser:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, default=settings.metadata_path)
    parser.add_argument("--database", type=Path, default=settings.database_path)
    parser.add_argument("--image-root", type=Path, default=settings.image_root)
    parser.add_argument("--source-revision", default=settings.dataset_revision)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument(
        "--report",
        type=Path,
        default=settings.artifact_root / "imports" / "polyvore_metadata_import.json",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not args.metadata.is_file():
        raise SystemExit(f"Metadata file not found: {args.metadata}")
    report = import_metadata(
        metadata_path=args.metadata,
        database_path=args.database,
        image_root=args.image_root,
        source_revision=args.source_revision,
        batch_size=args.batch_size,
    )
    write_json_atomic(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
