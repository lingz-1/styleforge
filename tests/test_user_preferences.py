import pytest

from styleforge.core.rubric import DEFAULT_EVALUATION_WEIGHTS
from styleforge.repositories.database import database_session, initialize_database
from styleforge.repositories.user_preferences_repository import (
    get_evaluation_weights,
    save_evaluation_weights,
)


def _db(db_dsn):
    initialize_database(db_dsn)
    return db_dsn


def test_default_weights_when_unset(db_dsn) -> None:
    path = _db(db_dsn)
    with database_session(path) as connection:
        weights = get_evaluation_weights(connection, "u")
    assert weights == DEFAULT_EVALUATION_WEIGHTS


def test_save_and_read_weights(db_dsn) -> None:
    path = _db(db_dsn)
    with database_session(path) as connection:
        saved = save_evaluation_weights(connection, "u", {"wearability": 0.5})
        loaded = get_evaluation_weights(connection, "u")
    assert loaded["wearability"] == pytest.approx(saved["wearability"], abs=1e-6)
    assert abs(sum(loaded.values()) - 1.0) < 1e-6


def test_save_normalizes_input(db_dsn) -> None:
    path = _db(db_dsn)
    with database_session(path) as connection:
        saved = save_evaluation_weights(connection, "u", {"wearability": 100})
    assert abs(sum(saved.values()) - 1.0) < 1e-6


def test_weights_scoped_by_user(db_dsn) -> None:
    path = _db(db_dsn)
    with database_session(path) as connection:
        save_evaluation_weights(connection, "a", {"wearability": 0.5})
        weights_a = get_evaluation_weights(connection, "a")
        weights_b = get_evaluation_weights(connection, "b")
    assert weights_a["wearability"] != weights_b["wearability"]
    assert weights_b == DEFAULT_EVALUATION_WEIGHTS


def test_save_requires_user_id(db_dsn) -> None:
    path = _db(db_dsn)
    with database_session(path) as connection:
        try:
            save_evaluation_weights(connection, "", {"wearability": 0.5})
            raised = False
        except ValueError:
            raised = True
    assert raised
