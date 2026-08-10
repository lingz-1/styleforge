"""SQLite connection and schema management."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA_VERSION = 9

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dataset_import_runs (
    run_id TEXT PRIMARY KEY,
    dataset_name TEXT NOT NULL,
    source_path TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    image_root TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    processed_count INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS dataset_sources (
    source TEXT PRIMARY KEY,
    image_root TEXT NOT NULL,
    metadata_path TEXT NOT NULL DEFAULT '',
    source_revision TEXT NOT NULL DEFAULT '',
    registered_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS catalog_items (
    item_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    gender TEXT NOT NULL,
    item_type TEXT NOT NULL,
    main_category TEXT NOT NULL,
    name TEXT NOT NULL,
    color TEXT NOT NULL,
    description TEXT NOT NULL,
    features_json TEXT NOT NULL,
    attributes_json TEXT NOT NULL DEFAULT '{}',
    image_filename TEXT NOT NULL,
    relative_image_path TEXT NOT NULL,
    image_status TEXT NOT NULL CHECK (image_status IN ('unbound', 'available', 'missing')),
    embedding_status TEXT NOT NULL CHECK (embedding_status IN ('pending', 'ready', 'failed')),
    raw_json_hash TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    imported_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_catalog_gender_type
ON catalog_items (gender, item_type);

CREATE INDEX IF NOT EXISTS idx_catalog_main_category
ON catalog_items (main_category);

CREATE INDEX IF NOT EXISTS idx_catalog_color
ON catalog_items (color);

CREATE TABLE IF NOT EXISTS catalog_item_images (
    item_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    image_role TEXT NOT NULL,
    image_filename TEXT NOT NULL,
    relative_image_path TEXT NOT NULL,
    image_status TEXT NOT NULL CHECK (image_status IN ('unbound', 'available', 'missing')),
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    PRIMARY KEY (item_id, position),
    UNIQUE (item_id, relative_image_path),
    FOREIGN KEY (item_id) REFERENCES catalog_items(item_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_catalog_item_images_item_role
ON catalog_item_images (item_id, image_role);

CREATE TABLE IF NOT EXISTS dataset_outfits (
    outfit_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    split TEXT,
    gender TEXT,
    name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    style TEXT NOT NULL DEFAULT '',
    season TEXT NOT NULL DEFAULT '',
    occasion TEXT NOT NULL DEFAULT '',
    theme TEXT NOT NULL DEFAULT '',
    color_palette_json TEXT NOT NULL DEFAULT '[]',
    is_official_outfit INTEGER NOT NULL DEFAULT 0 CHECK (is_official_outfit IN (0, 1)),
    is_official_look INTEGER NOT NULL DEFAULT 0 CHECK (is_official_look IN (0, 1)),
    source_revision TEXT NOT NULL,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dataset_outfit_items (
    outfit_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    item_id TEXT NOT NULL,
    item_description TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (outfit_id, position),
    UNIQUE (outfit_id, item_id),
    FOREIGN KEY (outfit_id) REFERENCES dataset_outfits(outfit_id) ON DELETE CASCADE,
    FOREIGN KEY (item_id) REFERENCES catalog_items(item_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_outfit_items_item
ON dataset_outfit_items (item_id);

CREATE TABLE IF NOT EXISTS wardrobe_items (
    user_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    favorite INTEGER NOT NULL DEFAULT 0 CHECK (favorite IN (0, 1)),
    notes TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL,
    PRIMARY KEY (user_id, item_id),
    FOREIGN KEY (item_id) REFERENCES catalog_items(item_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_wardrobe_user_active
ON wardrobe_items (user_id, active);

CREATE TABLE IF NOT EXISTS wardrobe_import_batches (
    batch_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    source_filename TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    parser_revision TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('previewed', 'committed', 'failed')),
    statistics_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT,
    created_at TEXT NOT NULL,
    committed_at TEXT,
    UNIQUE (user_id, platform, file_sha256)
);

CREATE INDEX IF NOT EXISTS idx_wardrobe_import_batches_user
ON wardrobe_import_batches (user_id, created_at);

CREATE TABLE IF NOT EXISTS wardrobe_import_rows (
    row_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    source_row_number INTEGER NOT NULL,
    row_hash TEXT NOT NULL,
    external_order_id_hash TEXT NOT NULL DEFAULT '',
    order_submitted_at TEXT NOT NULL DEFAULT '',
    order_status TEXT NOT NULL DEFAULT '',
    shop_name TEXT NOT NULL DEFAULT '',
    refund_status TEXT NOT NULL DEFAULT '',
    after_sale_status TEXT NOT NULL DEFAULT '',
    logistics_status TEXT NOT NULL DEFAULT '',
    order_eligibility TEXT NOT NULL DEFAULT 'unknown' CHECK (
        order_eligibility IN ('eligible', 'ineligible', 'unknown')
    ),
    order_eligibility_reason TEXT NOT NULL DEFAULT '',
    external_product_id TEXT NOT NULL DEFAULT '',
    product_name TEXT NOT NULL,
    canonical_url TEXT NOT NULL DEFAULT '',
    variant_text TEXT NOT NULL DEFAULT '',
    quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    listed_amount REAL,
    paid_amount REAL,
    currency TEXT NOT NULL DEFAULT 'CNY',
    predicted_item_type TEXT NOT NULL DEFAULT '',
    predicted_subtype TEXT NOT NULL DEFAULT '',
    predicted_color TEXT NOT NULL DEFAULT '',
    predicted_size TEXT NOT NULL DEFAULT '',
    predicted_audience TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0 CHECK (confidence >= 0 AND confidence <= 1),
    decision TEXT NOT NULL CHECK (
        decision IN ('candidate', 'excluded', 'committed', 'rejected')
    ),
    decision_reason TEXT NOT NULL DEFAULT '',
    catalog_item_id TEXT,
    UNIQUE (batch_id, source_row_number),
    FOREIGN KEY (batch_id) REFERENCES wardrobe_import_batches(batch_id) ON DELETE CASCADE,
    FOREIGN KEY (catalog_item_id) REFERENCES catalog_items(item_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_wardrobe_import_rows_batch_decision
ON wardrobe_import_rows (batch_id, decision, source_row_number);

CREATE TABLE IF NOT EXISTS personal_wardrobe_items (
    item_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    import_row_id TEXT UNIQUE,
    external_order_id_hash TEXT NOT NULL DEFAULT '',
    order_submitted_at TEXT NOT NULL DEFAULT '',
    order_status TEXT NOT NULL DEFAULT '',
    shop_name TEXT NOT NULL DEFAULT '',
    external_product_id TEXT NOT NULL DEFAULT '',
    variant_text TEXT NOT NULL DEFAULT '',
    quantity_owned INTEGER NOT NULL DEFAULT 1 CHECK (quantity_owned > 0),
    listed_amount REAL,
    paid_amount REAL,
    currency TEXT NOT NULL DEFAULT 'CNY',
    canonical_url TEXT NOT NULL DEFAULT '',
    ownership_status TEXT NOT NULL CHECK (
        ownership_status IN (
            'pending_confirmation', 'owned', 'returned', 'sold', 'discarded', 'gifted', 'lost'
        )
    ),
    review_status TEXT NOT NULL CHECK (review_status IN ('pending', 'confirmed', 'rejected')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (item_id) REFERENCES catalog_items(item_id) ON DELETE CASCADE,
    FOREIGN KEY (import_row_id) REFERENCES wardrobe_import_rows(row_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_personal_wardrobe_user_status
ON personal_wardrobe_items (user_id, ownership_status, review_status);

CREATE TABLE IF NOT EXISTS personal_item_embeddings (
    item_id TEXT PRIMARY KEY,
    embedding_kind TEXT NOT NULL CHECK (
        embedding_kind IN ('image', 'text', 'text_fallback')
    ),
    dimension INTEGER NOT NULL CHECK (dimension > 0),
    vector_blob BLOB NOT NULL,
    model_revision TEXT NOT NULL,
    embedded_at TEXT NOT NULL,
    FOREIGN KEY (item_id) REFERENCES personal_wardrobe_items(item_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id TEXT PRIMARY KEY,
    preference_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS styling_runs (
    run_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL,
    task_spec_json TEXT NOT NULL,
    result_json TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS candidate_outfits (
    run_id TEXT NOT NULL,
    outfit_id TEXT NOT NULL,
    rank INTEGER,
    score REAL NOT NULL,
    hard_valid INTEGER NOT NULL CHECK (hard_valid IN (0, 1)),
    item_ids_json TEXT NOT NULL,
    score_details_json TEXT NOT NULL,
    PRIMARY KEY (run_id, outfit_id),
    FOREIGN KEY (run_id) REFERENCES styling_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS request_memory (
    memory_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    request_signature_json TEXT NOT NULL,
    structure_signature_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_request_memory_user_created
ON request_memory (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS task_runs (
    run_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    task_type TEXT NOT NULL CHECK (
        task_type IN (
            'outfit_recommend', 'outfit_modify', 'style_advice',
            'item_advice', 'wardrobe_compatibility', 'wardrobe_gap'
        )
    ),
    request TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('running', 'completed', 'infeasible', 'needs_clarification', 'failed')
    ),
    context_pack_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_task_runs_user_created
ON task_runs (user_id, created_at DESC);
"""


def connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def initialize_database(database_path: Path) -> None:
    connection = connect(database_path)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        version_row = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        if version_row is not None and int(version_row["value"]) > SCHEMA_VERSION:
            raise RuntimeError(
                "Database schema is newer than this StyleForge build: "
                f"database={version_row['value']}, supported={SCHEMA_VERSION}"
            )
        connection.executescript(SCHEMA_SQL)
        _migrate_task_runs_status(connection)
        _ensure_column(connection, "dataset_outfits", "style", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "dataset_outfits", "season", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "dataset_outfits", "occasion", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "dataset_outfits", "theme", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(
            connection, "dataset_outfits", "color_palette_json", "TEXT NOT NULL DEFAULT '[]'"
        )
        _ensure_column(
            connection, "dataset_outfits", "is_official_outfit", "INTEGER NOT NULL DEFAULT 0"
        )
        _ensure_column(
            connection, "dataset_outfits", "is_official_look", "INTEGER NOT NULL DEFAULT 0"
        )
        _ensure_column(
            connection, "dataset_outfit_items", "item_description", "TEXT NOT NULL DEFAULT ''"
        )
        _ensure_column(
            connection, "catalog_items", "attributes_json", "TEXT NOT NULL DEFAULT '{}'"
        )
        _ensure_column(
            connection,
            "wardrobe_import_batches",
            "parser_revision",
            "TEXT NOT NULL DEFAULT ''",
        )
        for column_name, definition in (
            ("external_order_id_hash", "TEXT NOT NULL DEFAULT ''"),
            ("order_submitted_at", "TEXT NOT NULL DEFAULT ''"),
            ("order_status", "TEXT NOT NULL DEFAULT ''"),
            ("shop_name", "TEXT NOT NULL DEFAULT ''"),
            ("refund_status", "TEXT NOT NULL DEFAULT ''"),
            ("after_sale_status", "TEXT NOT NULL DEFAULT ''"),
            ("logistics_status", "TEXT NOT NULL DEFAULT ''"),
            ("order_eligibility", "TEXT NOT NULL DEFAULT 'unknown'"),
            ("order_eligibility_reason", "TEXT NOT NULL DEFAULT ''"),
        ):
            _ensure_column(
                connection,
                "wardrobe_import_rows",
                column_name,
                definition,
            )
        for column_name, definition in (
            ("external_order_id_hash", "TEXT NOT NULL DEFAULT ''"),
            ("order_submitted_at", "TEXT NOT NULL DEFAULT ''"),
            ("order_status", "TEXT NOT NULL DEFAULT ''"),
            ("shop_name", "TEXT NOT NULL DEFAULT ''"),
        ):
            _ensure_column(
                connection,
                "personal_wardrobe_items",
                column_name,
                definition,
            )
        _ensure_column(connection, "styling_runs", "semantic_detail_json", "TEXT")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_wardrobe_import_rows_order_eligibility "
            "ON wardrobe_import_rows (batch_id, order_eligibility, source_row_number)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _migrate_task_runs_status(connection: sqlite3.Connection) -> None:
    """Add needs_clarification to the task_runs status constraint in schema v8."""
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'task_runs'"
    ).fetchone()
    if row is None or "needs_clarification" in str(row["sql"]):
        return
    connection.execute(
        """
        CREATE TABLE task_runs_v8 (
            run_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            task_type TEXT NOT NULL CHECK (
                task_type IN (
                    'outfit_recommend', 'outfit_modify', 'style_advice',
                    'item_advice', 'wardrobe_compatibility', 'wardrobe_gap'
                )
            ),
            request TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN (
                    'running', 'completed', 'infeasible',
                    'needs_clarification', 'failed'
                )
            ),
            context_pack_json TEXT NOT NULL DEFAULT '{}',
            result_json TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            finished_at TEXT
        )
        """
    )
    connection.execute(
        """
        INSERT INTO task_runs_v8(
            run_id, user_id, task_type, request, status,
            context_pack_json, result_json, error_message, created_at, finished_at
        )
        SELECT
            run_id, user_id, task_type, request, status,
            context_pack_json, result_json, error_message, created_at, finished_at
        FROM task_runs
        """
    )
    connection.execute("DROP TABLE task_runs")
    connection.execute("ALTER TABLE task_runs_v8 RENAME TO task_runs")
    connection.execute(
        "CREATE INDEX idx_task_runs_user_created "
        "ON task_runs (user_id, created_at DESC)"
    )


def _ensure_column(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    definition: str,
) -> None:
    columns = {
        row["name"] for row in connection.execute(f"PRAGMA table_info({table_name})")
    }
    if column_name not in columns:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


@contextmanager
def database_session(database_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(database_path)
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
