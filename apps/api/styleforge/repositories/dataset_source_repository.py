"""Dataset-specific external image roots."""

from __future__ import annotations

from styleforge.repositories.database import Connection, Row
from datetime import datetime, timezone
from pathlib import Path


def register_dataset_source(
    connection: Connection,
    *,
    source: str,
    image_root: Path,
    metadata_path: Path | None = None,
    source_revision: str = "",
) -> None:
    """Register an external image root without moving any dataset files."""
    connection.execute(
        """
        INSERT INTO dataset_sources(
            source, image_root, metadata_path, source_revision, registered_at
        ) VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT(source) DO UPDATE SET
            image_root = excluded.image_root,
            metadata_path = excluded.metadata_path,
            source_revision = excluded.source_revision,
            registered_at = excluded.registered_at
        """,
        (
            source,
            str(image_root.resolve()),
            str(metadata_path.resolve()) if metadata_path is not None else "",
            source_revision,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def get_source_image_root(
    connection: Connection,
    source: str,
) -> Path | None:
    row = connection.execute(
        "SELECT image_root FROM dataset_sources WHERE source = %s",
        (source,),
    ).fetchone()
    return Path(row["image_root"]).resolve() if row is not None else None


def list_dataset_sources(connection: Connection) -> list[Row]:
    return list(connection.execute("SELECT * FROM dataset_sources ORDER BY source"))
