"""PostgreSQL-backed pytest fixtures.

Every database-touching test receives a fresh, isolated schema inside the
``styleforge_test`` database via the ``db_dsn`` / ``db_conn`` fixtures. The
schema is created per test and dropped on teardown, so tests never share rows.
``STYLEFORGE_TEST_DATABASE_DSN`` (set in ``.env``) points at the test database;
if it is unset these fixtures skip so unrelated tests still run.
"""

from __future__ import annotations

import os
import uuid
from typing import Iterator

import pytest

# Importing the config module loads the project ``.env`` (dotenv side effect),
# so ``STYLEFORGE_TEST_DATABASE_DSN`` set there is visible to the fixtures.
import styleforge.core.config  # noqa: F401
from styleforge.repositories.database import connect, database_session, initialize_database


def _scoped_dsn(dsn: str, schema: str) -> str:
    """DSN whose libpq ``options`` pins the connection to ``schema``."""
    separator = "&" if "?" in dsn else "?"
    return f"{dsn}{separator}options=-csearch_path%3D{schema}"


@pytest.fixture(scope="session")
def _test_dsn() -> str:
    dsn = os.environ.get("STYLEFORGE_TEST_DATABASE_DSN", "").strip()
    if not dsn:
        pytest.skip("STYLEFORGE_TEST_DATABASE_DSN is not set; database tests skipped")
    return dsn


@pytest.fixture(autouse=True)
def _legacy_modify_mode_default() -> Iterator[None]:
    """Keep the legacy OUTFIT_MODIFY chain the default for existing tests.

    Stage 4 primary-mode tests opt in explicitly with ``modify_mode="agentic"``
    (the explicit argument wins over the env flag); everything else keeps the
    old three-agent chain so pre-切流 behaviour is unchanged.
    """
    os.environ["STYLEFORGE_MODIFY_MODE"] = "legacy"
    yield
    os.environ.pop("STYLEFORGE_MODIFY_MODE", None)


@pytest.fixture()
def db_schema(_test_dsn: str) -> Iterator[str]:
    """A throwaway schema, created before and dropped after the test."""
    schema = f"test_{uuid.uuid4().hex[:12]}"
    admin = connect(_test_dsn)
    try:
        admin.execute(f'CREATE SCHEMA "{schema}"')
        admin.commit()
    finally:
        admin.close()
    yield schema
    admin = connect(_test_dsn)
    try:
        admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.commit()
    finally:
        admin.close()


@pytest.fixture()
def db_dsn(_test_dsn: str, db_schema: str) -> str:
    """Per-test PostgreSQL DSN isolated in its own (uninitialized) schema.

    Tests call ``initialize_database(db_dsn)`` themselves, mirroring the old
    ``initialize_database(tmp_path / "x.db")`` pattern, so a test that needs to
    control the schema version before initialization still can.
    """
    return _scoped_dsn(_test_dsn, db_schema)


@pytest.fixture()
def db_conn(db_dsn: str) -> Iterator:
    """A connection to an initialized, isolated schema."""
    initialize_database(db_dsn)
    with database_session(db_dsn) as connection:
        yield connection
