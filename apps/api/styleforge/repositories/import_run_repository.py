"""Audit trail for repeatable dataset imports."""

from __future__ import annotations

from styleforge.repositories.database import Connection
import uuid
from datetime import datetime, timezone
from pathlib import Path


def start_import_run(
    connection: Connection,
    source_path: Path,
    source_revision: str,
    image_root: Path | None,
    dataset_name: str = "Garments2Look-Polyvore-Items",
) -> str:
    run_id = str(uuid.uuid4())
    connection.execute(
        """
        INSERT INTO dataset_import_runs (
            run_id, dataset_name, source_path, source_revision, image_root,
            status, processed_count, started_at
        ) VALUES (%s, %s, %s, %s, %s, 'running', 0, %s)
        """,
        (
            run_id,
            dataset_name,
            str(source_path.resolve()),
            source_revision,
            str(image_root.resolve()) if image_root is not None else None,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    return run_id


def update_progress(connection: Connection, run_id: str, processed_count: int) -> None:
    connection.execute(
        "UPDATE dataset_import_runs SET processed_count = %s WHERE run_id = %s",
        (processed_count, run_id),
    )


def finish_import_run(
    connection: Connection,
    run_id: str,
    processed_count: int,
) -> None:
    connection.execute(
        """
        UPDATE dataset_import_runs
        SET status = 'completed', processed_count = %s, finished_at = %s
        WHERE run_id = %s
        """,
        (processed_count, datetime.now(timezone.utc).isoformat(), run_id),
    )


def fail_import_run(connection: Connection, run_id: str, error: BaseException) -> None:
    connection.execute(
        """
        UPDATE dataset_import_runs
        SET status = 'failed', error_message = %s, finished_at = %s
        WHERE run_id = %s
        """,
        (str(error)[:2000], datetime.now(timezone.utc).isoformat(), run_id),
    )
