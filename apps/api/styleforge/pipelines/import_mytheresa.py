"""Import Garments2Look-Mytheresa products and multi-view image metadata."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from styleforge.common.files import write_json_atomic
from styleforge.core.config import Settings
from styleforge.data.json_stream import iter_json_object
from styleforge.data.mytheresa import MytheresaImagePathResolver, normalize_mytheresa_item
from styleforge.repositories.catalog_repository import catalog_counts, upsert_items
from styleforge.repositories.database import (
    SCHEMA_VERSION,
    database_session,
    initialize_database,
)
from styleforge.repositories.dataset_source_repository import register_dataset_source
from styleforge.repositories.import_run_repository import (
    fail_import_run,
    finish_import_run,
    start_import_run,
    update_progress,
)
from styleforge.repositories.item_image_repository import replace_item_images


def _reject_write_path_inside_image_root(
    write_path: Path,
    image_root: Path,
    label: str,
) -> None:
    resolved_path = write_path.resolve()
    resolved_root = image_root.resolve()
    if resolved_path == resolved_root or resolved_path.is_relative_to(resolved_root):
        raise ValueError(f"{label} must not be inside the external image root")


def _reject_cross_source_collisions(connection, item_ids: list[str]) -> None:
    if not item_ids:
        return
    placeholders = ",".join("%s" for _ in item_ids)
    rows = connection.execute(
        f"SELECT item_id, source FROM catalog_items WHERE item_id IN ({placeholders})",  # noqa: S608
        item_ids,
    )
    collisions = [row["item_id"] for row in rows if row["source"] != "mytheresa"]
    if collisions:
        preview = ", ".join(collisions[:5])
        raise RuntimeError(f"Cross-source item ID collision: {preview}")


def import_mytheresa(
    *,
    metadata_path: Path,
    database_path: str,
    image_root: Path,
    source_revision: str,
    batch_size: int = 500,
    simulate_failure_after: int | None = None,
) -> dict[str, object]:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if simulate_failure_after is not None and simulate_failure_after < 1:
        raise ValueError("simulate_failure_after must be positive")
    if not metadata_path.is_file():
        raise FileNotFoundError(f"Metadata file not found: {metadata_path}")
    if not image_root.is_dir():
        raise FileNotFoundError(f"Image root not found: {image_root}")

    initialize_database(database_path)
    resolver = MytheresaImagePathResolver(image_root)
    with database_session(database_path) as connection:
        register_dataset_source(
            connection,
            source="mytheresa",
            image_root=image_root,
            metadata_path=metadata_path,
            source_revision=source_revision,
        )
        run_id = start_import_run(
            connection,
            metadata_path,
            source_revision,
            image_root,
            dataset_name="Garments2Look-Mytheresa-Items",
        )

    processed_count = 0
    image_count = 0
    try:
        item_batch = []
        image_batch = []
        for item_id, record in iter_json_object(metadata_path):
            item, images = normalize_mytheresa_item(item_id, record, resolver)
            item_batch.append(item)
            image_batch.extend(images)
            if len(item_batch) >= batch_size:
                with database_session(database_path) as connection:
                    _reject_cross_source_collisions(
                        connection,
                        [item.item_id for item in item_batch],
                    )
                    processed_count += upsert_items(
                        connection,
                        item_batch,
                        source_revision,
                    )
                    image_count += replace_item_images(connection, image_batch)
                    update_progress(connection, run_id, processed_count)
                item_batch.clear()
                image_batch.clear()
                if (
                    simulate_failure_after is not None
                    and processed_count >= simulate_failure_after
                ):
                    raise RuntimeError(
                        "Simulated import failure after committed batch: "
                        f"{processed_count} items"
                    )

        if item_batch:
            with database_session(database_path) as connection:
                _reject_cross_source_collisions(
                    connection,
                    [item.item_id for item in item_batch],
                )
                processed_count += upsert_items(connection, item_batch, source_revision)
                image_count += replace_item_images(connection, image_batch)
                update_progress(connection, run_id, processed_count)
            if (
                simulate_failure_after is not None
                and processed_count >= simulate_failure_after
            ):
                raise RuntimeError(
                    "Simulated import failure after committed batch: "
                    f"{processed_count} items"
                )

        with database_session(database_path) as connection:
            finish_import_run(connection, run_id, processed_count)
            counts = catalog_counts(connection)
            source_count = connection.execute(
                "SELECT COUNT(*) FROM catalog_items WHERE source = 'mytheresa'"
            ).fetchone()[0]
    except BaseException as error:
        with database_session(database_path) as connection:
            fail_import_run(connection, run_id, error)
        raise

    return {
        "schema_version": "styleforge.mytheresa-import.v1",
        "database_schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "status": "completed",
        "source_path": str(metadata_path.resolve()),
        "source_revision": source_revision,
        "image_root": str(image_root.resolve()),
        "processed_count": processed_count,
        "stored_image_records": image_count,
        "mytheresa_catalog_items": source_count,
        "database_path": str(database_path),
        "database_counts": counts,
    }


def build_parser() -> argparse.ArgumentParser:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--image-root", type=Path, required=True)
    parser.add_argument("--database", type=str, default=settings.database_dsn)
    parser.add_argument("--source-revision", default=settings.dataset_revision)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument(
        "--simulate-failure-after",
        type=int,
        help="Fault injection for a disposable database copy only.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=settings.artifact_root / "imports" / "mytheresa_metadata_import.json",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    _reject_write_path_inside_image_root(args.report, args.image_root, "report path")
    report = import_mytheresa(
        metadata_path=args.metadata,
        database_path=args.database,
        image_root=args.image_root,
        source_revision=args.source_revision,
        batch_size=args.batch_size,
        simulate_failure_after=args.simulate_failure_after,
    )
    write_json_atomic(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
