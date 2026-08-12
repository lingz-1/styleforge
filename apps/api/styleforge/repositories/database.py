"""PostgreSQL connection and schema management (StyleForge backend).

The SQLite backend has been retired. Connection helpers now talk to
PostgreSQL via psycopg3. Rows are returned as :class:`SqliteLikeRow`, a dict
subclass that also supports positional indexing (``row[0]``), mirroring the
old ``sqlite3.Row`` semantics so callers and repositories work unchanged.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import psycopg


SCHEMA_VERSION = 11


class SqliteLikeRow(dict):
    """A row dict that also supports positional access like ``sqlite3.Row``.

    ``row["column"]`` and ``row[0]`` both work, matching the previous SQLite
    row type so existing callers need no changes.
    """

    def __init__(self, *args: Any, _columns: list[str] | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._columns = _columns if _columns is not None else list(dict.__iter__(self))

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return dict.__getitem__(self, self._columns[key])
        return dict.__getitem__(self, key)


class PgConnection:
    """Thin wrapper adding sqlite3-style ``executemany`` to a psycopg3 Connection.

    psycopg3 puts ``executemany`` on the cursor, not the connection, while the
    retired SQLite backend exposed it on ``sqlite3.Connection`` and many call
    sites use ``connection.executemany(...)``. This proxy keeps those call
    sites working unchanged; every other attribute is delegated to the wrapped
    psycopg connection.
    """

    def __init__(self, raw: psycopg.Connection[Any]) -> None:
        self._raw = raw

    def executemany(self, sql: str, seq_of_params: Any) -> None:
        self._raw.cursor().executemany(sql, seq_of_params)

    def __enter__(self) -> PgConnection:
        # psycopg3 Connection is a context manager; expose the same protocol
        # on the proxy so ``with connect(dsn) as connection:`` works like the
        # old sqlite3.Connection. Implicit special-method lookup bypasses
        # ``__getattr__``, so these cannot be delegated.
        self._raw.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> Any:
        return self._raw.__exit__(exc_type, exc_value, traceback)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)


# Re-exported type aliases so repositories can annotate uniformly without
# depending on psycopg details.
Connection = PgConnection
Row = SqliteLikeRow


def sqlite_like_row_factory(cursor: psycopg.Cursor[Any]) -> Any:
    """psycopg3 row factory producing :class:`SqliteLikeRow` instances."""
    columns = [d.name for d in cursor.description] if cursor.description is not None else []

    def make_row(values: list[Any]) -> SqliteLikeRow:
        return SqliteLikeRow(zip(columns, values), _columns=columns)

    return make_row


# Keep in sync with artifacts/init_pg_schema.py. This is the PostgreSQL
# translation of the retired SCHEMA_SQL (SQLite dialect). Dialect notes:
#   - AUTOINCREMENT INTEGER PRIMARY KEY  -> BIGSERIAL PRIMARY KEY
#   - BLOB                              -> BYTEA
#   - chat_messages.message_seq BIGSERIAL replaces the implicit SQLite rowid
#     as the creation-order tiebreaker (rowid does not exist in PostgreSQL)
PG_SCHEMA_SQL = """
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

CREATE INDEX IF NOT EXISTS idx_wardrobe_import_rows_order_eligibility
ON wardrobe_import_rows (batch_id, order_eligibility, source_row_number);

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
    vector_blob BYTEA NOT NULL,
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
    finished_at TEXT,
    semantic_detail_json TEXT
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
    memory_id BIGSERIAL PRIMARY KEY,
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

CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_updated
ON chat_sessions (user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    message_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    task_type TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT '',
    result_json TEXT,
    created_at TEXT NOT NULL,
    message_seq BIGSERIAL,
    FOREIGN KEY (session_id) REFERENCES chat_sessions(session_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created
ON chat_messages (session_id, created_at, message_seq);

CREATE TABLE IF NOT EXISTS interaction_events (
    event_id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (
        event_type IN (
            'outfit_selected', 'outfit_rejected', 'item_replaced', 'item_rejected',
            'feedback_submitted', 'style_requested', 'compatibility_checked',
            'explicit_preference', 'wardrobe_adopted', 'wardrobe_removed'
        )
    ),
    item_id TEXT NOT NULL DEFAULT '',
    context_json TEXT NOT NULL DEFAULT '{}',
    features_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_interaction_events_user_created
ON interaction_events (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS preference_evidence (
    evidence_id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    dimension TEXT NOT NULL DEFAULT '',
    attribute TEXT NOT NULL,
    value TEXT NOT NULL,
    polarity TEXT NOT NULL CHECK (polarity IN ('positive', 'negative')),
    strength REAL NOT NULL DEFAULT 0.5 CHECK (strength >= 0 AND strength <= 1),
    scope_json TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL,
    event_id BIGINT REFERENCES interaction_events(event_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_preference_evidence_user_attr
ON preference_evidence (user_id, attribute, value);

CREATE TABLE IF NOT EXISTS preference_model (
    preference_id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    dimension TEXT NOT NULL DEFAULT '',
    attribute TEXT NOT NULL,
    value TEXT NOT NULL,
    polarity TEXT NOT NULL CHECK (polarity IN ('positive', 'negative')),
    lifecycle TEXT NOT NULL DEFAULT 'short_term' CHECK (
        lifecycle IN ('short_term', 'long_term_candidate', 'long_term')
    ),
    scope_json TEXT NOT NULL DEFAULT '{}',
    confidence REAL NOT NULL DEFAULT 0,
    support_score REAL NOT NULL DEFAULT 0,
    contradiction_score REAL NOT NULL DEFAULT 0,
    support_count INTEGER NOT NULL DEFAULT 0,
    contradiction_count INTEGER NOT NULL DEFAULT 0,
    source_summary_json TEXT NOT NULL DEFAULT '{}',
    decay_policy TEXT NOT NULL DEFAULT 'normal' CHECK (
        decay_policy IN ('none', 'slow', 'normal')
    ),
    last_observed_at TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (user_id, dimension, attribute, value)
);

CREATE INDEX IF NOT EXISTS idx_preference_model_user_active
ON preference_model (user_id, active, updated_at DESC);
"""


def connect(dsn: str) -> PgConnection:
    """Open a PostgreSQL connection with sqlite3.Row-like rows."""
    return PgConnection(psycopg.connect(dsn, row_factory=sqlite_like_row_factory))


def initialize_database(dsn: str) -> None:
    """Create the PostgreSQL schema if needed and check the schema version."""
    connection = connect(dsn)
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
        connection.execute(PG_SCHEMA_SQL)
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', %s) "
            "ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value",
            (str(SCHEMA_VERSION),),
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


@contextmanager
def database_session(dsn: str) -> Iterator[PgConnection]:
    """Context manager yielding a PostgreSQL connection with commit/rollback.

    Mirrors the old SQLite ``database_session(path)`` contract; callers pass a
    PostgreSQL DSN string instead of a file path.
    """
    connection = connect(dsn)
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
